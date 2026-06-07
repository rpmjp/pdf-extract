from datetime import datetime, timezone

from celery import Celery, Task
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .confidence import apply_disagreement_penalty, document_confidence, extraction_disagreement, transaction_confidence
from .config import settings
from .extract import classify_and_extract, extract_text_with_tesseract, render_pages_to_images
from .learning.examples import _infer_bank, _infer_layout
from .learning.fewshot import retrieve_few_shot
from .learning.rules import apply_rules
from .llm import extract_statement, extract_statement_from_images
from .models import AuditLog, Document, DocumentVersion, ParseJob, ReviewItem, Transaction
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
        source_for_ensemble = None
        images = None
        text = ""
        few_shot_examples = []
        if extracted["kind"] == "digital":
            text = "\n\n".join(p["text"] for p in extracted["pages"])
            if settings.few_shot_enabled:
                features = {
                    "bank": _infer_bank(text, doc),
                    "layout": _infer_layout(text),
                    "source": extracted["kind"],
                    "page_count": len(extracted["pages"]),
                    "transaction_count": 0,
                }
                few_shot_examples = retrieve_few_shot(db, features, k=settings.few_shot_k, exclude_document_id=doc.id)
                db.add(
                    AuditLog(
                        document_id=doc.id,
                        action="few_shot_retrieval",
                        actor="worker",
                        details={
                            "features": features,
                            "example_ids": [example["correction_example_id"] for example in few_shot_examples],
                            "example_document_ids": [example["document_id"] for example in few_shot_examples],
                        },
                    )
                )
                db.commit()
            source_for_ensemble = text
            result = extract_statement(text, few_shot_examples=few_shot_examples)
        else:
            images = render_pages_to_images(pdf_bytes)
            if settings.few_shot_enabled:
                features = {
                    "bank": "unknown",
                    "layout": "mixed",
                    "source": extracted["kind"],
                    "page_count": len(extracted["pages"]),
                    "transaction_count": 0,
                }
                few_shot_examples = retrieve_few_shot(db, features, k=settings.few_shot_k, exclude_document_id=doc.id)
                db.add(
                    AuditLog(
                        document_id=doc.id,
                        action="few_shot_retrieval",
                        actor="worker",
                        details={
                            "features": features,
                            "example_ids": [example["correction_example_id"] for example in few_shot_examples],
                            "example_document_ids": [example["document_id"] for example in few_shot_examples],
                        },
                    )
                )
                db.commit()
            result = extract_statement_from_images(images, few_shot_examples=few_shot_examples)

        corrections = correct_signs_from_balances(result)
        recon = reconcile(result)
        recon["sign_corrections"] = corrections
        confidence = document_confidence(result, document_kind=extracted["kind"], reconciliation_passed=recon["passed"])
        disagreement = {"score": 0, "checks": []}

        if extracted["kind"] == "scanned" and confidence < 0.75:
            preprocessed_images = render_pages_to_images(pdf_bytes, preprocess=True)
            preprocessed_result = extract_statement_from_images(preprocessed_images)
            preprocessed_corrections = correct_signs_from_balances(preprocessed_result)
            preprocessed_recon = reconcile(preprocessed_result)
            preprocessed_recon["sign_corrections"] = preprocessed_corrections
            preprocessed_confidence = document_confidence(
                preprocessed_result,
                document_kind="scanned",
                reconciliation_passed=preprocessed_recon["passed"],
            )
            if preprocessed_confidence > confidence:
                images = preprocessed_images
                result = preprocessed_result
                corrections = preprocessed_corrections
                recon = preprocessed_recon
                confidence = preprocessed_confidence

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
                    source_for_ensemble = fallback_text

        result, rule_corrections, rule_correction_details = apply_rules(result, {"text": source_for_ensemble or ""})
        corrections = correct_signs_from_balances(result)
        recon = reconcile(result)
        recon["sign_corrections"] = corrections
        recon["rule_corrections"] = rule_corrections
        confidence = document_confidence(result, document_kind=extracted["kind"], reconciliation_passed=recon["passed"])

        if settings.ensemble_confidence_enabled:
            if source_for_ensemble is not None:
                ensemble_result = extract_statement(source_for_ensemble, variant="ensemble")
            else:
                ensemble_result = extract_statement_from_images(images or render_pages_to_images(pdf_bytes), variant="ensemble")
            correct_signs_from_balances(ensemble_result)
            disagreement = extraction_disagreement(result, ensemble_result)
            confidence = apply_disagreement_penalty(confidence, disagreement)

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
        if disagreement["checks"]:
            sample = "; ".join(item["field"] for item in disagreement["checks"][:8])
            db.add(ReviewItem(document_id=doc.id, reason=f"LLM ensemble disagreement: {sample}"))

        result_payload = {
            "id": doc.id,
            "status": doc.status,
            "reconciliation": recon,
            "confidence_score": confidence,
            "ensemble_disagreement": disagreement,
            "rule_corrections": rule_correction_details,
            "extraction": result.model_dump(),
        }
        db.add(
            DocumentVersion(
                document_id=doc.id,
                source="llm_parse",
                actor="worker",
                data={**result_payload, "source_text": text},
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
