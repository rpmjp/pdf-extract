"""Document workflow routes.

This module owns the main statement lifecycle: upload, list, parse, review
edits, approval/rejection, PDF streaming, and document history. Most endpoints
open their own short-lived database session so request state is explicit and
background workers can use the same helpers without depending on FastAPI
dependency injection.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..app_limiter import limiter, user_limit_key
from ..auth import AuthUser, get_current_user, require_roles
from ..celery_helpers import ACTIVE_JOB_STATES, enqueue_parse_for_document
from ..db import SessionLocal
from ..extract import classify_and_extract
from ..learning.examples import create_correction_example_for_document
from ..models import AuditLog, Document, DocumentVersion, ParseJob, ReviewItem, Transaction
from ..query_helpers import apply_document_filters, apply_document_sort, paginate_documents
from ..reconcile import reconcile
from ..serializers import (
    add_audit,
    extraction_from_persisted,
    serialize_audit_log,
    serialize_document,
    serialize_document_version,
    serialize_parse_job,
    serialize_review_item,
    serialize_transaction,
)
from ..storage import get_object
from ..upload_helpers import persist_streamed_upload, stream_upload_to_temp

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Documents"])


class TransactionUpdate(BaseModel):
    """Reviewer-supplied replacement values for a single transaction row."""

    date: str
    description: str
    amount: float
    type: str
    balance: float | None = None


class RejectRequest(BaseModel):
    """Optional reviewer reason captured when a document is rejected."""

    reason: str | None = None


class BulkDocumentsRequest(BaseModel):
    """Batch action payload used by operations screens for multi-select work."""

    action: str
    document_ids: list[int]
    reason: str | None = None


def _recompute_review_items(db, doc: Document, transactions: list[Transaction]) -> dict:
    """Re-run deterministic reconciliation after reviewer edits.

    The LLM is not invoked here. Review edits should be fast and predictable:
    rebuild an extraction from persisted rows, reconcile it, replace open
    review items with the current failure reasons, and return the fresh result
    for audit metadata.
    """

    extraction = extraction_from_persisted(doc, transactions)
    recon = reconcile(extraction)
    recon["sign_corrections"] = 0
    db.query(ReviewItem).filter_by(document_id=doc.id, status="open").delete()
    if not recon["passed"]:
        failed = [c["detail"] for c in recon["checks"] if not c["passed"]]
        db.add(ReviewItem(document_id=doc.id, reason="; ".join(failed)))
    return recon


def _save_document_version(db, doc: Document, transactions: list[Transaction], source: str, actor: str):
    """Append a point-in-time snapshot before/after reviewer actions.

    Version rows are intentionally append-only. They give reviewers and auditors
    a durable trail of what the system believed at each stage without mutating
    the original extraction evidence.
    """

    db.add(
        DocumentVersion(
            document_id=doc.id,
            source=source,
            actor=actor,
            data={
                "document": serialize_document(doc),
                "transactions": [serialize_transaction(txn) for txn in transactions],
            },
        )
    )


@router.post("/documents")
@limiter.limit("50/hour", key_func=user_limit_key)
async def upload_document(
    request: Request,
    user: Annotated[AuthUser, Depends(require_roles("uploader"))],
    file: UploadFile = File(...),
):
    """Accept one PDF upload and persist it through the streaming upload path."""

    temp_path, sha256, _ = await stream_upload_to_temp(file)
    db = SessionLocal()
    try:
        doc = persist_streamed_upload(db, file.filename, temp_path, sha256)
        return serialize_document(doc)
    finally:
        temp_path.unlink(missing_ok=True)
        db.close()


@router.post("/documents/batch")
async def upload_documents_batch(
    user: Annotated[AuthUser, Depends(require_roles("uploader"))],
    files: list[UploadFile] = File(...),
):
    """Upload several PDFs independently and return per-file success/failure.

    A duplicate or invalid file should not poison the whole batch, so each file
    gets its own rollback boundary while sharing the same request-level session.
    """

    results = []
    db = SessionLocal()
    try:
        for file in files:
            temp_path = None
            try:
                temp_path, sha256, _ = await stream_upload_to_temp(file)
                doc = persist_streamed_upload(db, file.filename, temp_path, sha256)
                results.append({"filename": file.filename, "document": serialize_document(doc), "error": None})
            except HTTPException as exc:
                db.rollback()
                detail = str(exc.detail)
                duplicate_id = None
                if exc.status_code == 409:
                    raw_id = detail.split("id=")[-1].rstrip(")")
                    duplicate_id = int(raw_id) if raw_id.isdigit() else None
                results.append({"filename": file.filename, "document": None, "error": detail, "duplicate_id": duplicate_id})
            finally:
                if temp_path:
                    temp_path.unlink(missing_ok=True)
        return {"results": results}
    finally:
        db.close()


@router.get("/documents/stats")
def document_stats(user: Annotated[AuthUser, Depends(get_current_user)]):
    """Return small operational counters for the dashboard summary row."""

    from sqlalchemy import func
    db = SessionLocal()
    try:
        base = db.query(Document).filter(Document.status != "rejected")
        total = base.count()
        needs_review = base.filter(Document.status.in_(["needs_review", "failed"])).count()
        processing = base.filter(Document.status.in_(["queued", "parsing"])).count()
        avg_result = (
            db.query(func.avg(Document.confidence_score))
            .filter(Document.status != "rejected", Document.confidence_score.isnot(None))
            .scalar()
        )
        return {
            "total": total,
            "needs_review": needs_review,
            "processing": processing,
            "avg_confidence": float(avg_result) if avg_result is not None else None,
        }
    finally:
        db.close()


@router.post("/documents/bulk")
def bulk_documents(payload: BulkDocumentsRequest, user: Annotated[AuthUser, Depends(get_current_user)]):
    """Apply one reviewer/uploader action across selected documents.

    Each document is locked and committed independently so one bad row does not
    discard successful work on other selected rows.
    """

    if payload.action not in {"reparse", "approve", "reject"}:
        raise HTTPException(400, "action must be reparse, approve, or reject")
    if payload.action == "reparse" and "uploader" not in user.roles:
        raise HTTPException(403, "Uploader role required")
    if payload.action in {"approve", "reject"} and "reviewer" not in user.roles:
        raise HTTPException(403, "Reviewer role required")

    succeeded: list[int] = []
    failed: list[dict] = []
    db = SessionLocal()
    try:
        for doc_id in payload.document_ids:
            try:
                doc = db.query(Document).filter_by(id=doc_id).with_for_update().first()
                if not doc:
                    raise ValueError("Document not found")
                if payload.action == "reparse":
                    if doc.status in {"queued", "parsing"}:
                        raise ValueError("Document is already processing")
                    enqueue_parse_for_document(db, doc, user.username, "bulk")
                elif payload.action == "approve":
                    if doc.status != "verified":
                        raise ValueError("Only verified documents can be bulk approved")
                    previous_status = doc.status
                    doc.status = "approved"
                    review_items = db.query(ReviewItem).filter_by(document_id=doc.id, status="open").all()
                    for item in review_items:
                        item.status = "closed"
                    correction_example = create_correction_example_for_document(db, doc, user.username)
                    add_audit(
                        db,
                        doc.id,
                        "approve",
                        {"previous_status": previous_status, "closed_review_items": [item.id for item in review_items], "bulk": True},
                        user.username,
                    )
                    if correction_example:
                        add_audit(db, doc.id, "approve_learning", {"correction_example_id": correction_example.id, "bulk": True}, user.username)
                    db.commit()
                elif payload.action == "reject":
                    if doc.status not in {"needs_review", "failed"}:
                        raise ValueError("Only needs_review or failed documents can be bulk rejected")
                    previous_status = doc.status
                    doc.status = "rejected"
                    reason = payload.reason or "Rejected by reviewer"
                    db.add(ReviewItem(document_id=doc.id, reason=reason, status="closed"))
                    add_audit(db, doc.id, "reject", {"previous_status": previous_status, "reason": reason, "bulk": True}, user.username)
                    db.commit()
                succeeded.append(doc_id)
            except Exception as exc:
                db.rollback()
                failed.append({"id": doc_id, "error": str(exc)})
        return {"succeeded": succeeded, "failed": failed}
    finally:
        db.close()


@router.get("/documents")
def list_documents(
    user: Annotated[AuthUser, Depends(get_current_user)],
    priority: str | None = None,
    filter: str | None = None,
    q: str | None = None,
    sort: str | None = None,
    order: str | None = None,
    page: int = 1,
    per_page: int = 25,
):
    """Return paginated dashboard data with server-side filtering and sorting."""

    db = SessionLocal()
    try:
        query = db.query(Document).filter(Document.status != "rejected")
        query = apply_document_filters(query, q=q, filter_value=filter, priority=priority)
        query = apply_document_sort(query, sort=sort, order=order)
        return paginate_documents(query, page=page, per_page=per_page)
    finally:
        db.close()


@router.get("/documents/{doc_id}")
def get_document(doc_id: int, user: Annotated[AuthUser, Depends(get_current_user)]):
    """Return the complete trust-view payload for one document."""

    db = SessionLocal()
    try:
        doc = db.query(Document).filter_by(id=doc_id).first()
        if not doc:
            raise HTTPException(404, "Document not found")

        transactions = db.query(Transaction).filter_by(document_id=doc.id).order_by(Transaction.id).all()
        review_items = db.query(ReviewItem).filter_by(document_id=doc.id).order_by(ReviewItem.id).all()
        audit_log = db.query(AuditLog).filter_by(document_id=doc.id).order_by(AuditLog.created_at.desc()).all()
        parse_jobs = db.query(ParseJob).filter_by(document_id=doc.id).order_by(ParseJob.queued_at.desc()).all()
        versions = db.query(DocumentVersion).filter_by(document_id=doc.id).order_by(DocumentVersion.created_at.desc()).all()

        extraction = extraction_from_persisted(doc, transactions)
        recon = reconcile(extraction)
        recon["sign_corrections"] = 0
        if doc.opening_balance is None or doc.closing_balance is None:
            failed_details = [item.reason for item in review_items]
            recon["passed"] = doc.status == "verified"
            recon["checks"] = (
                [{"name": "stored_review", "passed": False, "detail": detail} for detail in failed_details]
                if failed_details
                else [{"name": "stored_status", "passed": doc.status == "verified", "detail": f"stored status is {doc.status}"}]
            )

        return {
            **serialize_document(doc),
            "reconciliation": recon,
            "transactions": [serialize_transaction(t) for t in transactions],
            "review_items": [serialize_review_item(item) for item in review_items],
            "audit_log": [serialize_audit_log(entry) for entry in audit_log],
            "parse_jobs": [serialize_parse_job(job) for job in parse_jobs],
            "versions": [serialize_document_version(version) for version in versions],
        }
    finally:
        db.close()


@router.get("/documents/{doc_id}/jobs")
def get_document_jobs(doc_id: int, user: Annotated[AuthUser, Depends(get_current_user)]):
    """Expose durable parse-job history for a document."""

    db = SessionLocal()
    try:
        if not db.query(Document).filter_by(id=doc_id).first():
            raise HTTPException(404, "Document not found")
        jobs = db.query(ParseJob).filter_by(document_id=doc_id).order_by(ParseJob.queued_at.desc()).all()
        return [serialize_parse_job(job) for job in jobs]
    finally:
        db.close()


@router.get("/documents/{doc_id}/versions")
def get_document_versions(doc_id: int, user: Annotated[AuthUser, Depends(require_roles("reviewer"))]):
    """Return append-only document snapshots visible to reviewers."""

    db = SessionLocal()
    try:
        if not db.query(Document).filter_by(id=doc_id).first():
            raise HTTPException(404, "Document not found")
        versions = db.query(DocumentVersion).filter_by(document_id=doc_id).order_by(DocumentVersion.created_at.desc()).all()
        return [serialize_document_version(version) for version in versions]
    finally:
        db.close()


@router.patch("/documents/{doc_id}/transactions/{txn_id}")
def update_transaction(
    doc_id: int,
    txn_id: int,
    payload: TransactionUpdate,
    user: Annotated[AuthUser, Depends(require_roles("reviewer"))],
):
    """Persist a reviewer transaction edit and re-evaluate reconciliation."""

    if payload.type not in {"deposit", "withdrawal"}:
        raise HTTPException(400, "Transaction type must be deposit or withdrawal")

    db = SessionLocal()
    try:
        doc = db.query(Document).filter_by(id=doc_id).with_for_update().first()
        if not doc:
            raise HTTPException(404, "Document not found")

        txn = db.query(Transaction).filter_by(id=txn_id, document_id=doc.id).first()
        if not txn:
            raise HTTPException(404, "Transaction not found")

        before = serialize_transaction(txn)
        txn.txn_date = payload.date
        txn.description = payload.description
        txn.amount = payload.amount
        txn.type = payload.type
        txn.balance = payload.balance
        after = serialize_transaction(txn)

        transactions = db.query(Transaction).filter_by(document_id=doc.id).order_by(Transaction.id).all()
        recon = _recompute_review_items(db, doc, transactions)

        changed = {
            key: {"before": before[key], "after": after[key]}
            for key in ["date", "description", "amount", "type", "balance"]
            if before[key] != after[key]
        }
        _save_document_version(db, doc, transactions, "review_edit", user.username)
        add_audit(
            db,
            doc.id,
            "edit_txn",
            {"transaction_id": txn.id, "changes": changed, "reconciliation_passed": recon["passed"]},
            user.username,
        )

        db.commit()
        db.refresh(txn)
        return serialize_transaction(txn)
    finally:
        db.close()


@router.post("/documents/{doc_id}/approve")
def approve_document(doc_id: int, user: Annotated[AuthUser, Depends(require_roles("reviewer"))]):
    """Approve a document and capture learning data from any corrections."""

    db = SessionLocal()
    try:
        doc = db.query(Document).filter_by(id=doc_id).first()
        if not doc:
            raise HTTPException(404, "Document not found")

        previous_status = doc.status
        doc.status = "approved"
        review_items = db.query(ReviewItem).filter_by(document_id=doc.id, status="open").all()
        for item in review_items:
            item.status = "closed"

        try:
            correction_example = create_correction_example_for_document(db, doc, user.username)
        except Exception:
            logger.exception("Failed to create correction example for approved document doc_id=%s actor=%s", doc.id, user.username)
            raise
        add_audit(db, doc.id, "approve", {"previous_status": previous_status, "closed_review_items": [item.id for item in review_items]}, user.username)
        if correction_example:
            add_audit(db, doc.id, "approve_learning", {"correction_example_id": correction_example.id}, user.username)
        db.commit()
        db.refresh(doc)
        return serialize_document(doc)
    finally:
        db.close()


@router.post("/documents/{doc_id}/reject")
def reject_document(
    doc_id: int,
    user: Annotated[AuthUser, Depends(require_roles("reviewer"))],
    payload: RejectRequest | None = None,
):
    """Mark a document rejected while preserving the reviewer-facing reason."""

    db = SessionLocal()
    try:
        doc = db.query(Document).filter_by(id=doc_id).first()
        if not doc:
            raise HTTPException(404, "Document not found")

        previous_status = doc.status
        doc.status = "rejected"
        reason = payload.reason if payload and payload.reason else "Rejected by reviewer"
        db.add(ReviewItem(document_id=doc.id, reason=reason, status="closed"))

        add_audit(db, doc.id, "reject", {"previous_status": previous_status, "reason": reason}, user.username)
        db.commit()
        db.refresh(doc)
        return serialize_document(doc)
    finally:
        db.close()


@router.get("/documents/{doc_id}/file")
def get_document_file(
    doc_id: int,
    user: Annotated[AuthUser, Depends(get_current_user)],
    range_header: str | None = Header(default=None, alias="Range"),
):
    """Stream the source PDF inline for browser PDF viewers.

    Range support matters because PDF viewers often request byte slices for
    pagination/seek behavior. The object is still read from MinIO as bytes in
    this implementation, then sliced for HTTP range responses.
    """

    db = SessionLocal()
    try:
        doc = db.query(Document).filter_by(id=doc_id).first()
        if not doc:
            raise HTTPException(404, "Document not found")
        add_audit(db, doc.id, "view_file", {"range": bool(range_header), "filename": doc.filename}, user.username)
        db.commit()
        pdf_bytes = get_object(doc.minio_key)
        file_size = len(pdf_bytes)
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Disposition": f'inline; filename="{doc.filename}"',
            "Content-Length": str(file_size),
        }

        if range_header:
            unit, _, requested_range = range_header.partition("=")
            if unit.strip().lower() != "bytes":
                raise HTTPException(416, "Unsupported range unit")

            start_text, _, end_text = requested_range.partition("-")
            try:
                start = int(start_text) if start_text else 0
                end = int(end_text) if end_text else file_size - 1
            except ValueError:
                raise HTTPException(416, "Invalid range")

            if start >= file_size or end < start:
                raise HTTPException(416, "Invalid range")

            end = min(end, file_size - 1)
            body = pdf_bytes[start : end + 1]
            headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
            headers["Content-Length"] = str(len(body))
            return Response(content=body, media_type="application/pdf", headers=headers, status_code=206)

        return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)
    finally:
        db.close()


@router.post("/documents/{doc_id}/extract")
def extract_document(doc_id: int, user: Annotated[AuthUser, Depends(require_roles("uploader"))]):
    """Run only the low-level extraction classifier for diagnostics."""

    db = SessionLocal()
    try:
        doc = db.query(Document).filter_by(id=doc_id).first()
        if not doc:
            raise HTTPException(404, "Document not found")
        pdf_bytes = get_object(doc.minio_key)
        result = classify_and_extract(pdf_bytes)
        doc.status = f"extracted:{result['kind']}"
        db.commit()
        return {
            "id": doc.id,
            "kind": result["kind"],
            "pages": len(result["pages"]),
            "preview": result["pages"][0]["text"][:300] if result["pages"] else "",
        }
    finally:
        db.close()


@router.post("/documents/{doc_id}/parse")
@limiter.limit("20/hour", key_func=user_limit_key)
def parse_document(request: Request, doc_id: int, user: Annotated[AuthUser, Depends(require_roles("uploader"))]):
    """Queue a parse job instead of blocking the HTTP request on the LLM."""

    db = SessionLocal()
    try:
        doc = db.query(Document).filter_by(id=doc_id).with_for_update().first()
        if not doc:
            raise HTTPException(404, "Document not found")

        body, created = enqueue_parse_for_document(db, doc, user.username, "single")
        if created:
            return JSONResponse(body, status_code=status.HTTP_202_ACCEPTED)
        return body
    finally:
        db.close()
