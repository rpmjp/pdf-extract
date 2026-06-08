"""Celery job state, idempotency, and recovery helpers.

The dashboard and upload flow need stable job semantics even when a worker is
restarted or killed mid-parse.  These helpers reconcile Celery's transient Redis
state with durable database fields on ``documents`` and ``parse_jobs``.
"""
import logging
import uuid

import redis
from celery.result import AsyncResult

from .config import settings
from .db import SessionLocal
from .models import Document, ParseJob
from .serializers import add_audit
from .worker import celery_app, parse_document_task

logger = logging.getLogger(__name__)

ACTIVE_JOB_STATES = {"PENDING", "RECEIVED", "STARTED", "RETRY"}
RECOVERABLE_DOCUMENT_STATES = {"queued", "parsing"}
JOB_RECOVERY_LOCK_SECONDS = 30


def broker_has_pending_job(job_id: str) -> bool:
    """Return True when the raw Redis broker queue still contains the task id."""
    client = redis.Redis(host=settings.redis_host, port=settings.redis_port, db=0)
    job_id_bytes = job_id.encode()
    for message in client.lrange("celery", 0, -1):
        if job_id_bytes in message:
            return True
    return False


def celery_queue_depth() -> int:
    client = redis.Redis(host=settings.redis_host, port=settings.redis_port, db=0)
    return client.llen("celery")


def celery_inspect_has_job(job_id: str) -> bool:
    """Ask live Celery workers whether a job is active, reserved, or scheduled."""
    inspector = celery_app.control.inspect(timeout=1)

    def task_matches(task: dict) -> bool:
        request = task.get("request", task)
        return task.get("id") == job_id or request.get("id") == job_id

    for get_tasks in (inspector.active, inspector.reserved, inspector.scheduled):
        try:
            workers = get_tasks() or {}
        except Exception:
            continue
        for tasks in workers.values():
            if any(task_matches(task) for task in tasks):
                return True
    return False


def celery_has_known_job(job_id: str) -> bool:
    return broker_has_pending_job(job_id) or celery_inspect_has_job(job_id)


def recover_orphaned_job(job_id: str) -> bool:
    """Requeue a job that appears active in the DB but is unknown to Celery."""
    if celery_has_known_job(job_id):
        return False

    db = SessionLocal()
    try:
        doc = db.query(Document).filter_by(current_job_id=job_id).first()
        if not doc or doc.status not in RECOVERABLE_DOCUMENT_STATES:
            return False

        client = redis.Redis(host=settings.redis_host, port=settings.redis_port, db=0)
        lock_key = f"job-recovery:{job_id}"
        if not client.set(lock_key, "1", nx=True, ex=JOB_RECOVERY_LOCK_SECONDS):
            return True

        doc.status = "queued"
        job = db.query(ParseJob).filter_by(id=job_id).first()
        if job:
            job.status = "queued"
            job.error = None
        db.commit()
        parse_document_task.apply_async(args=[doc.id], task_id=job_id)
        return True
    finally:
        db.close()


def serialize_job(job_id: str) -> dict:
    """Map Celery state into the API's small polling response shape."""
    result = AsyncResult(job_id, app=celery_app)
    state = result.state
    recovered = False
    if state == "STARTED" and recover_orphaned_job(job_id):
        state = "PENDING"
        recovered = True
    elif state == "PENDING" and not broker_has_pending_job(job_id) and recover_orphaned_job(job_id):
        recovered = True

    if state in {"PENDING", "RECEIVED"}:
        status = "queued"
    elif state == "STARTED":
        status = "started"
    elif state == "RETRY":
        status = "retrying"
    elif state == "SUCCESS":
        status = "success"
    elif state == "FAILURE":
        status = "failed"
    else:
        status = state.lower()

    payload = {"job_id": job_id, "status": status}
    if recovered:
        payload["recovered"] = True
    if state == "SUCCESS":
        payload["result"] = result.result
    elif state == "FAILURE":
        payload["error"] = str(result.result)
    return payload


def enqueue_parse_for_document(db, doc: Document, actor: str = "system", source: str = "single"):
    """Queue parsing once, returning an existing active job when appropriate."""
    if doc.current_job_id:
        existing = AsyncResult(doc.current_job_id, app=celery_app)
        if existing.state == "PENDING" and broker_has_pending_job(doc.current_job_id):
            return serialize_job(doc.current_job_id), False
        if existing.state in ACTIVE_JOB_STATES - {"PENDING"}:
            return serialize_job(doc.current_job_id), False
        doc.current_job_id = None

    job_id = str(uuid.uuid4())
    doc.status = "queued"
    doc.current_job_id = job_id
    db.add(ParseJob(id=job_id, document_id=doc.id, status="queued", retry_count=0))
    add_audit(db, doc.id, "parse_queued", {"job_id": job_id, "source": source}, actor)
    db.commit()
    parse_document_task.apply_async(args=[doc.id], task_id=job_id)
    return {"job_id": job_id, "status": "queued"}, True
