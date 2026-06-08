"""Shared document filtering, sorting, and pagination helpers."""
from fastapi import HTTPException
from sqlalchemy import case

from .models import Document
from .serializers import serialize_document


def priority_sort_expr():
    """SQL expression matching the API's computed priority order."""
    return case(
        (Document.status == "failed", 1),
        ((Document.status == "needs_review") & (Document.confidence_score < 0.6), 1),
        (Document.status == "needs_review", 2),
        (Document.status.in_(["verified", "approved"]), 3),
        else_=4,
    )


SORT_FIELDS = {
    "id": Document.id,
    "filename": Document.filename,
    "account_holder": Document.account_holder,
    "priority": priority_sort_expr(),
    "status": Document.status,
    "confidence_score": Document.confidence_score,
    "created_at": Document.created_at,
}


def apply_document_filters(query, *, q: str | None = None, filter_value: str | None = None, priority: str | None = None):
    """Apply dashboard/review filters while keeping endpoints aligned."""
    from sqlalchemy import or_
    if q:
        pattern = f"%{q.strip()}%"
        query = query.filter(or_(Document.filename.ilike(pattern), Document.account_holder.ilike(pattern)))
    if filter_value == "needs_review":
        query = query.filter(Document.status.in_(["needs_review", "failed"]))
    elif filter_value == "processing":
        query = query.filter(Document.status.in_(["queued", "parsing"]))
    elif filter_value:
        raise HTTPException(400, "filter must be needs_review or processing")
    if priority == "P1":
        query = query.filter((Document.status == "failed") | ((Document.status == "needs_review") & (Document.confidence_score < 0.6)))
    elif priority == "P2":
        query = query.filter((Document.status == "needs_review") & ((Document.confidence_score == None) | (Document.confidence_score >= 0.6)))  # noqa: E711
    elif priority == "P3":
        query = query.filter(Document.status.in_(["verified", "approved"]))
    elif priority is not None:
        raise HTTPException(400, "priority must be P1, P2, or P3")
    return query


def apply_document_sort(query, *, sort: str | None = None, order: str | None = None):
    """Apply validated sort parameters or the default priority-first order."""
    if sort is None:
        return query.order_by(priority_sort_expr().asc(), Document.created_at.desc())
    if sort not in SORT_FIELDS:
        raise HTTPException(400, "unsupported sort field")
    if order not in {"asc", "desc"}:
        raise HTTPException(400, "order must be asc or desc")
    column = SORT_FIELDS[sort]
    return query.order_by(column.asc() if order == "asc" else column.desc(), Document.created_at.desc())


def paginate_documents(query, *, page: int = 1, per_page: int = 25, serializer=serialize_document):
    """Return a stable paginated response envelope for document lists."""
    if page < 1:
        raise HTTPException(400, "page must be >= 1")
    if per_page not in {25, 50, 100}:
        raise HTTPException(400, "per_page must be 25, 50, or 100")
    total = query.order_by(None).count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    return {
        "items": [serializer(item) for item in items],
        "total": total,
        "page": page,
        "per_page": per_page,
    }
