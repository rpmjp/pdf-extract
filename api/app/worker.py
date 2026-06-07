from celery import Celery, Task
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .config import settings
from .extract import classify_and_extract, render_pages_to_images
from .llm import extract_statement, extract_statement_from_images
from .models import Document, ReviewItem, Transaction
from .reconcile import correct_signs_from_balances, reconcile
from .storage import get_object

celery_app = Celery(
    "pdf_extract",
    broker=settings.redis_url,
    backend=settings.redis_result_url,
)
celery_app.conf.update(
    task_track_started=True,
    result_expires=60 * 60 * 24,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)


def mark_failed(doc_id: int, message: str):
    db = SessionLocal()
    try:
        doc = db.query(Document).filter_by(id=doc_id).first()
        if doc:
            doc.status = "failed"
            doc.current_job_id = None
            db.add(ReviewItem(document_id=doc.id, reason=message[:1000], status="open"))
            db.commit()
    finally:
        db.close()


class ParseTask(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        if args:
            mark_failed(args[0], str(exc))
        super().on_failure(exc, task_id, args, kwargs, einfo)


@celery_app.task(
    bind=True,
    base=ParseTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 2, "countdown": 5},
)
def parse_document_task(self, doc_id: int):
    db = SessionLocal()
    try:
        doc = db.query(Document).filter_by(id=doc_id).first()
        if not doc:
            raise ValueError("Document not found")

        doc.status = "parsing"
        doc.current_job_id = self.request.id
        db.commit()

        pdf_bytes = get_object(doc.minio_key)
        extracted = classify_and_extract(pdf_bytes)
        if extracted["kind"] == "digital":
            text = "\n\n".join(p["text"] for p in extracted["pages"])
            result = extract_statement(text)
        else:
            images = render_pages_to_images(pdf_bytes)
            result = extract_statement_from_images(images)

        corrections = correct_signs_from_balances(result)
        recon = reconcile(result)
        recon["sign_corrections"] = corrections

        doc.status = "verified" if recon["passed"] else "needs_review"
        doc.current_job_id = None
        doc.account_holder = result.account_holder
        doc.account_number = result.account_number
        doc.statement_period = result.statement_period
        doc.opening_balance = result.opening_balance
        doc.closing_balance = result.closing_balance

        db.query(Transaction).filter_by(document_id=doc.id).delete()
        db.query(ReviewItem).filter_by(document_id=doc.id).delete()

        for t in result.transactions:
            db.add(
                Transaction(
                    document_id=doc.id,
                    txn_date=t.date,
                    description=t.description,
                    amount=t.amount,
                    type=t.type,
                    balance=t.balance,
                )
            )

        if not recon["passed"]:
            failed = [c["detail"] for c in recon["checks"] if not c["passed"]]
            db.add(ReviewItem(document_id=doc.id, reason="; ".join(failed)))

        db.commit()
        return {
            "id": doc.id,
            "status": doc.status,
            "reconciliation": recon,
            "extraction": result.model_dump(),
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()