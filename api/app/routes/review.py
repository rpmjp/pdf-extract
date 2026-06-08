"""Reviewer work-queue routes."""

from typing import Annotated

from fastapi import APIRouter, Depends

from ..auth import AuthUser, require_roles
from ..db import SessionLocal
from ..models import Document, ReviewItem
from ..query_helpers import apply_document_filters, apply_document_sort, paginate_documents
from ..serializers import serialize_document, serialize_review_item

router = APIRouter(tags=["Review"])


@router.get("/review-queue")
def review_queue(
    user: Annotated[AuthUser, Depends(require_roles("reviewer"))],
    q: str | None = None,
    sort: str | None = None,
    order: str | None = None,
    page: int = 1,
    per_page: int = 25,
):
    """Return only documents that need human attention, with issue metadata."""

    db = SessionLocal()
    try:
        query = db.query(Document).filter(Document.status.in_(["needs_review", "failed"]))
        query = apply_document_filters(query, q=q)
        query = apply_document_sort(query, sort=sort, order=order)

        def serialize_review_doc(doc: Document):
            """Attach lightweight review-item signals to each queued document."""

            review_items = db.query(ReviewItem).filter_by(document_id=doc.id, status="open").order_by(ReviewItem.id).all()
            return {
                **serialize_document(doc),
                "review_item_count": len(review_items),
                "first_review_reason": review_items[0].reason if review_items else None,
            }

        return paginate_documents(query, page=page, per_page=per_page, serializer=serialize_review_doc)
    finally:
        db.close()
