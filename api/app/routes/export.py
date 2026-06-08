"""
Evidence export: GET /documents/{id}/export

Returns a ZIP archive containing every artefact an auditor needs to reconstruct
a document's full life-cycle without touching the live database:

  {filename}.pdf              — original uploaded PDF from MinIO
  extraction_original.json   — first LLM output (source=llm_parse)
  extraction_final.json      — current extracted state + transactions
  transactions.csv           — current transactions, CSV format
  audit_log.json             — every audit entry for this document
  versions.json              — every document_version snapshot
  corrections.json           — correction_example if one exists
  parse_jobs.json            — every parse_job record
  manifest.json              — SHA-256 of each file + HMAC-signed manifest hash

Requires: admin role.
Produces: an audit entry ("export") for every successful call.
"""
import csv
import hashlib
import hmac
import io
import json
import logging
import zipfile
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..audit_chain import add_audit_entry
from ..auth import AuthUser, require_roles
from ..config import settings
from ..db import SessionLocal
from ..models import (
    AuditLog,
    CorrectionExample,
    Document,
    DocumentVersion,
    ParseJob,
    Transaction,
)
from ..serializers import (
    serialize_audit_log,
    serialize_document,
    serialize_document_version,
    serialize_parse_job,
    serialize_transaction,
)
from ..storage import get_object

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Export"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sign_manifest(body: dict, key: str) -> str:
    """HMAC-SHA256 of the canonical JSON of *body* (sorted keys, compact)."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hmac.new(key.encode(), canonical.encode(), hashlib.sha256).hexdigest()


def _json_bytes(obj) -> bytes:
    return json.dumps(obj, indent=2, default=str, ensure_ascii=False).encode()


def _transactions_csv(transactions: list[Transaction]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["date", "description", "amount", "type", "balance", "confidence"],
        lineterminator="\n",
    )
    writer.writeheader()
    for t in transactions:
        writer.writerow({
            "date": t.txn_date,
            "description": t.description,
            "amount": float(t.amount),
            "type": t.type,
            "balance": float(t.balance) if t.balance is not None else "",
            "confidence": float(t.confidence) if t.confidence is not None else "",
        })
    return buf.getvalue().encode()


def _correction_data(db, doc_id: int) -> list[dict]:
    examples = (
        db.query(CorrectionExample)
        .filter_by(document_id=doc_id)
        .order_by(CorrectionExample.id)
        .all()
    )
    return [
        {
            "id": ex.id,
            "document_id": ex.document_id,
            "original_version_id": ex.original_version_id,
            "corrected_version_id": ex.corrected_version_id,
            "field_diffs": ex.field_diffs,
            "failure_category": ex.failure_category,
            "pdf_features": ex.pdf_features,
            "created_at": ex.created_at.isoformat(),
        }
        for ex in examples
    ]


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.get("/documents/{doc_id}/export")
def export_document(
    doc_id: int,
    user: Annotated[AuthUser, Depends(require_roles("admin"))],
):
    """
    Download a self-contained evidence ZIP for a single document.

    The ZIP includes the original PDF, all extraction snapshots, a transaction CSV,
    the full audit log, correction examples, parse job records, and a cryptographically
    signed manifest that lets an auditor verify file integrity without re-querying the DB.
    """
    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        if doc is None:
            raise HTTPException(404, f"Document {doc_id} not found")

        now = datetime.now(timezone.utc)
        export_key = settings.audit_export_key or settings.jwt_secret or "dev-audit-export-key"

        # ── Fetch all data inside one session ─────────────────────────────────
        transactions = (
            db.query(Transaction)
            .filter_by(document_id=doc_id)
            .order_by(Transaction.id)
            .all()
        )
        audit_entries = (
            db.query(AuditLog)
            .filter(AuditLog.document_id == doc_id)
            .order_by(AuditLog.id)
            .all()
        )
        versions = (
            db.query(DocumentVersion)
            .filter_by(document_id=doc_id)
            .order_by(DocumentVersion.id)
            .all()
        )
        parse_jobs = (
            db.query(ParseJob)
            .filter_by(document_id=doc_id)
            .order_by(ParseJob.queued_at)
            .all()
        )

        # First LLM parse snapshot
        original_version = next(
            (v for v in versions if v.source == "llm_parse"),
            None,
        )

        # ── Build the ZIP in memory ───────────────────────────────────────────
        buf = io.BytesIO()
        file_hashes: dict[str, str] = {}

        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:

            # 1 — Original PDF
            pdf_filename = doc.filename or f"document_{doc_id}.pdf"
            try:
                pdf_bytes = get_object(doc.minio_key)
            except Exception as exc:
                logger.warning("export: could not fetch PDF for doc %s: %s", doc_id, exc)
                pdf_bytes = b""
            zf.writestr(pdf_filename, pdf_bytes)
            file_hashes[pdf_filename] = _sha256_bytes(pdf_bytes)

            # 2 — extraction_original.json
            original_data = original_version.data if original_version else {}
            original_bytes = _json_bytes(original_data)
            zf.writestr("extraction_original.json", original_bytes)
            file_hashes["extraction_original.json"] = _sha256_bytes(original_bytes)

            # 3 — extraction_final.json
            final_data = {
                **serialize_document(doc),
                "transactions": [serialize_transaction(t) for t in transactions],
            }
            final_bytes = _json_bytes(final_data)
            zf.writestr("extraction_final.json", final_bytes)
            file_hashes["extraction_final.json"] = _sha256_bytes(final_bytes)

            # 4 — transactions.csv
            csv_bytes = _transactions_csv(transactions)
            zf.writestr("transactions.csv", csv_bytes)
            file_hashes["transactions.csv"] = _sha256_bytes(csv_bytes)

            # 5 — audit_log.json
            audit_bytes = _json_bytes([serialize_audit_log(e) for e in audit_entries])
            zf.writestr("audit_log.json", audit_bytes)
            file_hashes["audit_log.json"] = _sha256_bytes(audit_bytes)

            # 6 — versions.json
            versions_bytes = _json_bytes([serialize_document_version(v) for v in versions])
            zf.writestr("versions.json", versions_bytes)
            file_hashes["versions.json"] = _sha256_bytes(versions_bytes)

            # 7 — corrections.json
            corrections_bytes = _json_bytes(_correction_data(db, doc_id))
            zf.writestr("corrections.json", corrections_bytes)
            file_hashes["corrections.json"] = _sha256_bytes(corrections_bytes)

            # 8 — parse_jobs.json
            jobs_bytes = _json_bytes([serialize_parse_job(j) for j in parse_jobs])
            zf.writestr("parse_jobs.json", jobs_bytes)
            file_hashes["parse_jobs.json"] = _sha256_bytes(jobs_bytes)

            # 9 — manifest.json (signed)
            manifest_body = {
                "document_id": doc_id,
                "exported_at": now.isoformat(),
                "exported_by": user.username,
                "files": file_hashes,
            }
            manifest_body["manifest_signature"] = _sign_manifest(
                {k: v for k, v in manifest_body.items() if k != "manifest_signature"},
                export_key,
            )
            manifest_bytes = _json_bytes(manifest_body)
            zf.writestr("manifest.json", manifest_bytes)

        # ── Audit entry ───────────────────────────────────────────────────────
        add_audit_entry(
            db,
            doc_id,
            "export",
            {
                "filename": doc.filename,
                "exported_by": user.username,
                "exported_at": now.isoformat(),
                "file_count": len(file_hashes),
            },
            user.username,
        )
        db.commit()

        # ── Stream ZIP back to the caller ─────────────────────────────────────
        buf.seek(0)
        safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in doc.filename)
        dl_name = f"evidence_doc{doc_id}_{safe_name}.zip"

        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{dl_name}"'},
        )

    except HTTPException:
        raise
    except Exception:
        logger.exception("export: unexpected error for doc %s", doc_id)
        raise HTTPException(500, "Export failed — see server logs")
    finally:
        db.close()
