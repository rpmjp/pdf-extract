from datetime import datetime, timezone

from celery import Celery, Task
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .confidence import document_confidence, transaction_confidence
from .config import settings
from .extract import classify_and_extract, extract_text_with_tesseract, render_pages_to_images
from .llm import extract_statement, extract_statement_from_images
from .models import Document, DocumentVersion, ParseJob, ReviewItem, Transaction
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
            job_id = doc.current_job_id
            doc.status = "failed"
            doc.current_job_id = None
            if job_id:
                job = db.query(ParseJob).filter_by(id=job_id).first()
                if job:
                    job.status = "failed"
                    job.finished_at = datetime.now(timezone.utc)
                    job.error = message[:4000]
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
        job = db.query(ParseJob).filter_by(id=self.request.id).first()
        if not job:
            job = ParseJob(id=self.request.id, document_id=doc.id, status="parsing", retry_count=0)
            db.add(job)
        job.status = "parsing"
        job.started_at = datetime.now(timezone.utc)
        job.worker_name = self.request.hostname
        job.retry_count = self.request.retries
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
        confidence = document_confidence(result, document_kind=extracted["kind"], reconciliation_passed=recon["passed"])

        if settings.ocr_fallback_enabled and extracted["kind"] == "scanned" and confidence < 0.75:
            fallback_text = extract_text_with_tesseract(pdf_bytes)
            if fallback_text:
                fallback_result = extract_statement(fallback_text)
                fallback_corrections = correct_signs_from_balances(fallback_result)
                fallback_recon = reconcile(fallback_result)
                fallback_recon["sign_corrections"] = fallback_corrections
                fallback_confidence = document_confidence(
                    fallback_result,
                    document_kind="digital",
                    reconciliation_passed=fallback_recon["passed"],
                )
                if fallback_confidence > confidence:
                    result = fallback_result
                    corrections = fallback_corrections
                    recon = fallback_recon
                    confidence = fallback_confidence

        doc.status = "verified" if recon["passed"] else "needs_review"
        doc.current_job_id = None
        doc.account_holder = result.account_holder
        doc.account_number = result.account_number
        doc.statement_period = result.statement_period
        doc.opening_balance = result.opening_balance
        doc.closing_balance = result.closing_balance
        doc.confidence_score = confidence

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
                    confidence=transaction_confidence(
                        t,
                        document_kind=extracted["kind"],
                        reconciliation_passed=recon["passed"],
                    ),
                )
            )

        if not recon["passed"]:
            failed = [c["detail"] for c in recon["checks"] if not c["passed"]]
            db.add(ReviewItem(document_id=doc.id, reason="; ".join(failed)))

        result_payload = {
            "id": doc.id,
            "status": doc.status,
            "reconciliation": recon,
            "confidence_score": confidence,
            "extraction": result.model_dump(),
        }
        db.add(
            DocumentVersion(
                document_id=doc.id,
                source="llm_parse",
                actor="worker",
                data=result_payload,
            )
        )
        job.status = "success"
        job.finished_at = datetime.now(timezone.utc)
        job.error = None
        job.result = result_payload

        db.commit()
        return result_payload
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
