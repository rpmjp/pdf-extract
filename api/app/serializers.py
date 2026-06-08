"""API serialization helpers.

These functions define the public JSON shape of documents, transactions, audit
records, parse jobs, and versions.  Keeping serialization centralized prevents
route modules from accidentally drifting in field names or computed values.
"""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from .audit_chain import add_audit_entry
from .models import AuditLog, Document, DocumentVersion, ParseJob, ReviewItem, Transaction
from .priority import compute_priority
from .schemas import StatementExtraction
from .schemas import Transaction as TransactionSchema


def parse_since(value: str) -> datetime:
    """Parse admin/insight time windows such as ``30d`` or ISO datetimes."""
    if value.endswith("d") and value[:-1].isdigit():
        return datetime.now(timezone.utc) - timedelta(days=int(value[:-1]))
    if value.endswith("w") and value[:-1].isdigit():
        return datetime.now(timezone.utc) - timedelta(weeks=int(value[:-1]))
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(400, "since must be like 30d, 8w, or an ISO datetime")


def week_key(value: datetime) -> str:
    year, week, _ = value.isocalendar()
    return f"{year}-W{week:02d}"


def add_audit(db, doc_id: int | None, action: str, details: dict, actor: str):
    """Compatibility wrapper around the hash-chained audit insert helper."""
    add_audit_entry(db, doc_id, action, details, actor)


def serialize_document(doc: Document) -> dict:
    """Serialize a document with computed priority and decrypted PII fields."""
    return {
        "id": doc.id,
        "filename": doc.filename,
        "status": doc.status,
        "scan_status": doc.scan_status,
        "current_job_id": doc.current_job_id,
        "sha256": doc.sha256,
        "created_at": doc.created_at.isoformat(),
        "account_holder": doc.account_holder,
        "account_number": doc.account_number,
        "statement_period": doc.statement_period,
        "opening_balance": float(doc.opening_balance) if doc.opening_balance is not None else None,
        "closing_balance": float(doc.closing_balance) if doc.closing_balance is not None else None,
        "confidence_score": float(doc.confidence_score) if doc.confidence_score is not None else None,
        "priority": compute_priority(doc.status, doc.confidence_score),
    }


def serialize_transaction(t: Transaction) -> dict:
    return {
        "id": t.id,
        "date": t.txn_date,
        "description": t.description,
        "amount": float(t.amount),
        "type": t.type,
        "balance": float(t.balance) if t.balance is not None else None,
        "confidence": float(t.confidence) if t.confidence is not None else None,
    }


def serialize_review_item(item: ReviewItem) -> dict:
    return {
        "id": item.id,
        "reason": item.reason,
        "status": item.status,
        "created_at": item.created_at.isoformat(),
    }


def serialize_audit_log(entry: AuditLog) -> dict:
    return {
        "id": entry.id,
        "document_id": entry.document_id,
        "action": entry.action,
        "details": entry.details,
        "actor": entry.actor,
        "created_at": entry.created_at.isoformat(),
        "hash_chain": entry.hash_chain,
    }


def serialize_parse_job(job: ParseJob) -> dict:
    return {
        "id": job.id,
        "document_id": job.document_id,
        "status": job.status,
        "queued_at": job.queued_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "error": job.error,
        "retry_count": job.retry_count,
        "worker_name": job.worker_name,
        "result": job.result,
    }


def serialize_document_version(version: DocumentVersion) -> dict:
    return {
        "id": version.id,
        "document_id": version.document_id,
        "source": version.source,
        "actor": version.actor,
        "data": version.data,
        "created_at": version.created_at.isoformat(),
    }


def extraction_from_persisted(doc: Document, transactions: list[Transaction]) -> StatementExtraction:
    """Rehydrate persisted rows into the Pydantic extraction model."""
    return StatementExtraction(
        account_holder=doc.account_holder,
        account_number=doc.account_number,
        statement_period=doc.statement_period,
        opening_balance=float(doc.opening_balance) if doc.opening_balance is not None else None,
        closing_balance=float(doc.closing_balance) if doc.closing_balance is not None else None,
        transactions=[
            TransactionSchema(
                date=t.txn_date,
                description=t.description,
                amount=float(t.amount),
                type=t.type,
                balance=float(t.balance) if t.balance is not None else None,
            )
            for t in transactions
        ],
    )
