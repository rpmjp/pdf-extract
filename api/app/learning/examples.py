from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import fitz

from ..extract import classify_and_extract
from ..learning.categorize import categorize_failure
from ..models import AuditLog, CorrectionExample, Document, DocumentVersion, Transaction
from ..storage import get_object


DOC_FIELDS = ["account_holder", "account_number", "statement_period", "opening_balance", "closing_balance"]
TXN_FIELDS = ["date", "description", "amount", "type", "balance"]
logger = logging.getLogger(__name__)


def _current_extraction(doc: Document, transactions: list[Transaction]) -> dict[str, Any]:
    return {
        "account_holder": doc.account_holder,
        "account_number": doc.account_number,
        "statement_period": doc.statement_period,
        "opening_balance": float(doc.opening_balance) if doc.opening_balance is not None else None,
        "closing_balance": float(doc.closing_balance) if doc.closing_balance is not None else None,
        "transactions": [
            {
                "date": txn.txn_date,
                "description": txn.description,
                "amount": float(txn.amount),
                "type": txn.type,
                "balance": float(txn.balance) if txn.balance is not None else None,
            }
            for txn in transactions
        ],
    }


def extraction_from_version(version: DocumentVersion) -> dict[str, Any]:
    data = version.data or {}
    if "extraction" in data:
        return data["extraction"]
    document = data.get("document") or {}
    return {
        "account_holder": document.get("account_holder"),
        "account_number": document.get("account_number"),
        "statement_period": document.get("statement_period"),
        "opening_balance": document.get("opening_balance"),
        "closing_balance": document.get("closing_balance"),
        "transactions": data.get("transactions") or [],
    }


def find_original_version(db, doc_id: int) -> DocumentVersion | None:
    original = (
        db.query(DocumentVersion)
        .filter_by(document_id=doc_id, source="llm_parse")
        .order_by(DocumentVersion.created_at.asc(), DocumentVersion.id.asc())
        .first()
    )
    if original:
        return original
    return db.query(DocumentVersion).filter_by(document_id=doc_id).order_by(DocumentVersion.created_at.asc(), DocumentVersion.id.asc()).first()


def diff_extractions(original: dict[str, Any], corrected: dict[str, Any]) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    for field in DOC_FIELDS:
        before = original.get(field)
        after = corrected.get(field)
        if before != after:
            diffs.append({"path": f"root.{field}", "transaction_index": None, "before": before, "after": after})

    original_txns = original.get("transactions") or []
    corrected_txns = corrected.get("transactions") or []
    max_len = max(len(original_txns), len(corrected_txns))
    for index in range(max_len):
        before_txn = original_txns[index] if index < len(original_txns) else None
        after_txn = corrected_txns[index] if index < len(corrected_txns) else None
        if before_txn is None or after_txn is None:
            diffs.append({"path": f"transactions[{index}]", "transaction_index": index, "before": before_txn, "after": after_txn})
            continue
        for field in TXN_FIELDS:
            before = before_txn.get(field)
            after = after_txn.get(field)
            if before != after:
                diffs.append({"path": f"transactions[{index}].{field}", "transaction_index": index, "before": before, "after": after})
    return diffs


def _infer_bank(text: str, doc: Document) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.lower().startswith(("account", "statement", "opening", "closing", "date")):
            return stripped[:120]
    if doc.account_number:
        return doc.account_number[:64]
    return "unknown"


def _infer_layout(text: str) -> str:
    lower = text.lower()
    has_table = "date" in lower and ("debit" in lower or "credit" in lower) and "balance" in lower
    has_prose = " credited " in lower or " debited " in lower or lower.count(" on ") >= 2
    if has_table and has_prose:
        return "mixed"
    if has_table:
        return "tabular"
    if has_prose:
        return "prose"
    return "mixed"


def compute_pdf_features(doc: Document, transaction_count: int) -> dict[str, Any]:
    try:
        pdf_bytes = get_object(doc.minio_key)
        page_count = fitz.open(stream=pdf_bytes, filetype="pdf").page_count
        extracted = classify_and_extract(pdf_bytes)
        text = "\n".join(page.get("text", "") for page in extracted.get("pages", []))
        return {
            "bank": _infer_bank(text, doc),
            "layout": _infer_layout(text),
            "source": extracted.get("kind", "digital"),
            "page_count": page_count,
            "transaction_count": transaction_count,
        }
    except Exception as exc:
        return {
            "bank": "unknown",
            "layout": "mixed",
            "source": "digital",
            "page_count": 0,
            "transaction_count": transaction_count,
            "feature_error": exc.__class__.__name__,
        }


def create_correction_example_for_document(db, doc: Document, actor: str) -> CorrectionExample | None:
    logger.warning("create_correction_example_for_document entered doc_id=%s actor=%s", doc.id, actor)
    existing = db.query(CorrectionExample).filter_by(document_id=doc.id).first()
    if existing:
        logger.info("correction example already exists doc_id=%s correction_example_id=%s", doc.id, existing.id)
        return existing

    transactions = db.query(Transaction).filter_by(document_id=doc.id).order_by(Transaction.id).all()
    original_version = find_original_version(db, doc.id)
    if not original_version:
        original_version = DocumentVersion(
            document_id=doc.id,
            source="approval_original_snapshot",
            actor=actor,
            data={"extraction": _current_extraction(doc, transactions)},
        )
        db.add(original_version)
        db.flush()

    corrected_version = DocumentVersion(
        document_id=doc.id,
        source="approval_snapshot",
        actor=actor,
        data={
            "document": {
                "account_holder": doc.account_holder,
                "account_number": doc.account_number,
                "statement_period": doc.statement_period,
                "opening_balance": float(doc.opening_balance) if doc.opening_balance is not None else None,
                "closing_balance": float(doc.closing_balance) if doc.closing_balance is not None else None,
            },
            "transactions": _current_extraction(doc, transactions)["transactions"],
        },
    )
    db.add(corrected_version)
    db.flush()

    original = extraction_from_version(original_version)
    corrected = extraction_from_version(corrected_version)
    diffs = diff_extractions(original, corrected)
    features = compute_pdf_features(doc, len(corrected.get("transactions") or []))
    category = "other" if features.get("feature_error") else categorize_failure(original, diffs)

    example = CorrectionExample(
        document_id=doc.id,
        original_version_id=original_version.id,
        corrected_version_id=corrected_version.id,
        field_diffs=diffs,
        failure_category=category,
        pdf_features=features,
        created_at=datetime.now(timezone.utc),
    )
    db.add(example)
    db.flush()
    db.add(
        AuditLog(
            document_id=doc.id,
            action="correction_example",
            actor=actor,
            details={"correction_example_id": example.id, "failure_category": category},
        )
    )
    return example
