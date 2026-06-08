"""Celery worker tasks for parsing, audit verification, and retention.

The API enqueues work and returns quickly; this module performs the expensive
LLM/OCR/reconciliation pipeline in a separate process.  Each task opens its own
database session because worker processes do not share FastAPI request scope.
"""
import logging
from datetime import datetime, timezone

from celery import Celery, Task

logger = logging.getLogger(__name__)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .confidence import apply_disagreement_penalty, document_confidence, extraction_disagreement, transaction_confidence
from .config import settings
from .extract import classify_and_extract, extract_text_with_tesseract, render_pages_to_images
from .learning.examples import _infer_bank, _infer_layout
from .learning.fewshot import retrieve_few_shot
from .learning.rules import apply_rules
from .llm import extract_statement, extract_statement_from_images
from .logging_utils import configure_pii_logging
from .audit_chain import add_audit_entry, verify_chain
from .models import AuditVerification, Document, DocumentVersion, ParseJob, RetentionPolicy, ReviewItem, Transaction
from .reconcile import correct_signs_from_balances, reconcile
from .storage import get_object

settings.validate_production_safety()

celery_app = Celery(
    "pdf_extract",
    broker=settings.redis_url,
    backend=settings.redis_result_url,
)
configure_pii_logging()
celery_app.conf.update(
    task_track_started=True,
    result_expires=60 * 60 * 24,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    beat_schedule={
        "rescan-unscanned-documents": {
            "task": "app.worker.rescan_documents",
            "schedule": 60 * 60,          # every hour
        },
        "verify-audit-chain": {
            "task": "app.worker.verify_audit_chain_task",
            "schedule": 60 * 60 * 24,     # daily
        },
        "enforce-retention": {
            "task": "app.worker.enforce_retention",
            "schedule": 60 * 60 * 24,     # daily
        },
    },
)

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)


def mark_failed(doc_id: int, message: str):
    """Mark a document/job as failed after Celery exhausts retries."""
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
    """Celery Task base that persists terminal failure state."""
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
    """Run the complete extraction pipeline for one uploaded document."""
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

        # Fetch the immutable source artifact. All downstream derived rows can
        # be regenerated from this object plus the current prompt/model config.
        pdf_bytes = get_object(doc.minio_key)
        extracted = classify_and_extract(pdf_bytes)
        source_for_ensemble = None
        images = None
        text = ""
        few_shot_examples = []
        # Digital PDFs use the embedded text layer. Scanned PDFs are rendered to
        # images and sent through the multimodal extraction path.
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
                add_audit_entry(
                    db,
                    doc.id,
                    "few_shot_retrieval",
                    {
                        "features": features,
                        "example_ids": [example["correction_example_id"] for example in few_shot_examples],
                        "example_document_ids": [example["document_id"] for example in few_shot_examples],
                    },
                    "worker",
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
                add_audit_entry(
                    db,
                    doc.id,
                    "few_shot_retrieval",
                    {
                        "features": features,
                        "example_ids": [example["correction_example_id"] for example in few_shot_examples],
                        "example_document_ids": [example["document_id"] for example in few_shot_examples],
                    },
                    "worker",
                )
                db.commit()
            result = extract_statement_from_images(images, few_shot_examples=few_shot_examples)

        corrections = correct_signs_from_balances(result)
        recon = reconcile(result)
        recon["sign_corrections"] = corrections
        confidence = document_confidence(result, document_kind=extracted["kind"], reconciliation_passed=recon["passed"])
        disagreement = {"score": 0, "checks": []}

        # Low-confidence scans get one preprocessing pass. We only keep the
        # preprocessed result if deterministic confidence improves.
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

        # Tesseract is a targeted fallback, not the primary path. It helps when
        # the vision model struggles but OCR can still recover usable text.
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

        # Rules are deterministic post-processors learned from review failures.
        # They run before final reconciliation so corrected data gets scored.
        result, rule_corrections, rule_correction_details = apply_rules(result, {"text": source_for_ensemble or ""})
        corrections = correct_signs_from_balances(result)
        recon = reconcile(result)
        recon["sign_corrections"] = corrections
        recon["rule_corrections"] = rule_corrections
        confidence = document_confidence(result, document_kind=extracted["kind"], reconciliation_passed=recon["passed"])

        # Ensemble disagreement is used as a grounded uncertainty signal. The
        # second pass does not overwrite values; it only penalizes confidence
        # and creates review items when material fields disagree.
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


@celery_app.task(name="app.worker.rescan_documents")
def rescan_documents():
    """Rescan documents with scan_status 'unscanned' or 'scan_error' via ClamAV."""
    from .security.scanner import ScannerUnavailable, scan_document

    if not settings.clamav_enabled:
        return {"skipped": True, "reason": "clamav_disabled"}

    db = SessionLocal()
    scanned = infected = errors = 0
    try:
        docs = (
            db.query(Document)
            .filter(Document.scan_status.in_(["unscanned", "scan_error"]))
            .all()
        )
        for doc in docs:
            try:
                data = get_object(doc.minio_key)
                result = scan_document(data)
                doc.scan_status = "clean" if result.clean else "infected"
                if not result.clean:
                    infected += 1
                    logger.warning(
                        "rescan_infected doc_id=%s filename=%r virus=%r",
                        doc.id,
                        doc.filename,
                        result.virus_name,
                    )
                else:
                    scanned += 1
            except ScannerUnavailable:
                doc.scan_status = "unscanned"
                errors += 1
            except Exception as exc:
                doc.scan_status = "scan_error"
                errors += 1
                logger.warning("rescan_error doc_id=%s: %s", doc.id, exc)
            db.commit()
        return {"scanned": scanned, "infected": infected, "errors": errors}
    finally:
        db.close()


@celery_app.task(name="app.worker.verify_audit_chain_task")
def verify_audit_chain_task():
    """Walk the full audit hash chain and record the result in audit_verifications."""
    db = SessionLocal()
    try:
        result = verify_chain(db)
        from datetime import datetime, timezone
        verification = AuditVerification(
            verified_at=datetime.now(timezone.utc),
            since=None,
            rows_checked=result["rows_checked"],
            chain_intact=result["chain_intact"],
            first_break_id=result["first_break_id"],
        )
        db.add(verification)
        db.commit()
        if not result["chain_intact"]:
            logger.error(
                "AUDIT CHAIN BREAK DETECTED first_break_id=%s rows_checked=%d",
                result["first_break_id"],
                result["rows_checked"],
            )
        return result
    finally:
        db.close()


@celery_app.task(name="app.worker.enforce_retention")
def enforce_retention():
    """Delete (tombstone) documents that have exceeded their retention period."""
    from datetime import datetime, timedelta, timezone

    db = SessionLocal()
    deleted = skipped = errors = 0
    try:
        policies = (
            db.query(RetentionPolicy)
            .filter(RetentionPolicy.is_active == True)  # noqa: E712
            .all()
        )
        now = datetime.now(timezone.utc)

        for policy in policies:
            statuses = [s.strip() for s in policy.status_pattern.split(",") if s.strip()]
            cutoff = now - timedelta(days=policy.retention_days)

            expired_docs = (
                db.query(Document)
                .filter(
                    Document.status.in_(statuses),
                    Document.created_at < cutoff,
                    Document.deleted_at.is_(None),
                )
                .all()
            )

            for doc in expired_docs:
                try:
                    # Delete from MinIO first
                    from .storage import s3, settings as _settings
                    try:
                        s3.delete_object(Bucket=_settings.minio_bucket, Key=doc.minio_key)
                    except Exception as exc:
                        logger.warning("retention_minio_delete_failed doc_id=%s: %s", doc.id, exc)

                    # Log deletion BEFORE clearing fields so the audit entry has full context
                    add_audit_entry(
                        db,
                        doc.id,
                        "retention_deleted",
                        {
                            "filename": doc.filename,
                            "sha256": doc.sha256,
                            "original_status": doc.status,
                            "policy_id": policy.id,
                            "policy_status_pattern": policy.status_pattern,
                            "retention_days": policy.retention_days,
                            "created_at": doc.created_at.isoformat(),
                        },
                        "retention_policy",
                    )

                    # Tombstone: clear sensitive data, mark deleted
                    doc.account_holder = None
                    doc.account_number = None
                    doc.minio_key = f"deleted/{doc.sha256}.pdf"
                    doc.status = "retention_deleted"
                    doc.deleted_at = now
                    doc.deletion_policy_id = policy.id

                    db.commit()
                    deleted += 1
                    logger.info(
                        "retention_deleted doc_id=%s filename=%r policy_id=%s",
                        doc.id, doc.filename, policy.id,
                    )
                except Exception as exc:
                    db.rollback()
                    errors += 1
                    logger.error("retention_delete_error doc_id=%s: %s", doc.id, exc)

        return {"deleted": deleted, "skipped": skipped, "errors": errors}
    finally:
        db.close()
