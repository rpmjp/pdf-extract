import hashlib
import logging
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Annotated

from celery.result import AsyncResult
from fastapi import Depends, FastAPI, UploadFile, File, HTTPException, Response, Header, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel
from sqlalchemy import case, create_engine, func, inspect, or_, text
from sqlalchemy.orm import sessionmaker
import redis

from .auth import AuthUser, LoginRequest, authenticate, configure_auth, create_access_token, get_current_user, require_roles, seed_dev_users
from .config import settings
from .learning.examples import create_correction_example_for_document
from .models import AuditLog, CorrectionExample, Document, DocumentVersion, ParseJob, Transaction, ReviewItem
from .priority import compute_priority
from .storage import ensure_bucket, put_object, get_object
from .extract import classify_and_extract, render_pages_to_images
from .llm import extract_statement, extract_statement_from_images
from .reconcile import reconcile, correct_signs_from_balances
from .schemas import StatementExtraction, Transaction as TransactionSchema
from .worker import celery_app, parse_document_task

logger = logging.getLogger(__name__)

app = FastAPI(title="PDF Extract API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Accept-Ranges", "Content-Length", "Content-Range", "Content-Disposition"],
)


@app.middleware("http")
async def record_metrics(request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    path = route.path if route else request.url.path
    REQUEST_COUNT.labels(request.method, path, str(response.status_code)).inc()
    REQUEST_LATENCY.labels(request.method, path).observe(time.perf_counter() - start)
    return response


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)
configure_auth(SessionLocal)

REQUEST_COUNT = Counter("pdf_extract_http_requests_total", "HTTP requests", ["method", "path", "status"])
REQUEST_LATENCY = Histogram("pdf_extract_http_request_seconds", "HTTP request latency", ["method", "path"])
QUEUE_DEPTH = Gauge("pdf_extract_celery_queue_depth", "Celery broker queue depth")


class TransactionUpdate(BaseModel):
    date: str
    description: str
    amount: float
    type: str
    balance: float | None = None


class RejectRequest(BaseModel):
    reason: str | None = None


class BatchUploadResult(BaseModel):
    filename: str
    document: dict | None = None
    error: str | None = None
    duplicate_id: int | None = None


class BulkDocumentsRequest(BaseModel):
    action: str
    document_ids: list[int]
    reason: str | None = None


ACTIVE_JOB_STATES = {"PENDING", "RECEIVED", "STARTED", "RETRY"}
RECOVERABLE_DOCUMENT_STATES = {"queued", "parsing"}
JOB_RECOVERY_LOCK_SECONDS = 30


def parse_since(value: str) -> datetime:
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


def broker_has_pending_job(job_id: str) -> bool:
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


def serialize_document(doc: Document) -> dict:
    return {
        "id": doc.id,
        "filename": doc.filename,
        "status": doc.status,
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


def extraction_from_persisted(doc: Document, transactions: list[Transaction]) -> StatementExtraction:
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


def add_audit(db, doc_id: int, action: str, details: dict, actor: str):
    db.add(AuditLog(document_id=doc_id, action=action, details=details, actor=actor))


def validate_pdf_upload(file_name: str, data: bytes, content_type: str | None = None):
    if not file_name.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files accepted")
    if content_type and content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(400, "Only PDF files accepted")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(400, "Only valid PDF files accepted")
    if len(data) > settings.max_pdf_bytes:
        max_mb = settings.max_pdf_bytes // (1024 * 1024)
        raise HTTPException(413, f"PDF is too large; maximum size is {max_mb} MB")


def persist_upload(db, file_name: str, data: bytes, content_type: str | None = None) -> Document:
    validate_pdf_upload(file_name, data, content_type)

    sha256 = hashlib.sha256(data).hexdigest()
    existing = db.query(Document).filter_by(sha256=sha256).first()
    if existing:
        raise HTTPException(409, f"Document already exists (id={existing.id})")

    key = f"{sha256}.pdf"
    put_object(key, data)
    doc = Document(filename=file_name, sha256=sha256, minio_key=key)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def recompute_review_items(db, doc: Document, transactions: list[Transaction]) -> dict:
    extraction = extraction_from_persisted(doc, transactions)
    recon = reconcile(extraction)
    recon["sign_corrections"] = 0

    db.query(ReviewItem).filter_by(document_id=doc.id, status="open").delete()
    if not recon["passed"]:
        failed = [c["detail"] for c in recon["checks"] if not c["passed"]]
        db.add(ReviewItem(document_id=doc.id, reason="; ".join(failed)))

    return recon


def save_document_version(db, doc: Document, transactions: list[Transaction], source: str, actor: str):
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


@app.on_event("startup")
def startup():
    settings.validate_production_safety()
    ensure_bucket()
    if inspect(engine).has_table("users"):
        seed_dev_users()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/deps")
def health_deps():
    deps = {}
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        deps["postgres"] = "ok"
    except Exception as e:
        deps["postgres"] = f"error: {e.__class__.__name__}"
    try:
        r = redis.Redis(host=settings.redis_host, port=settings.redis_port)
        r.ping()
        deps["redis"] = "ok"
    except Exception as e:
        deps["redis"] = f"error: {e.__class__.__name__}"
    return deps


@app.get("/metrics")
def metrics():
    try:
        QUEUE_DEPTH.set(celery_queue_depth())
    except Exception:
        QUEUE_DEPTH.set(-1)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/auth/login")
def login(payload: LoginRequest):
    user = authenticate(payload.username, payload.password)
    return {"access_token": create_access_token(user), "token_type": "bearer", "user": user.model_dump()}


@app.get("/auth/me")
def me(user: Annotated[AuthUser, Depends(get_current_user)]):
    return user


@app.get("/jobs/{job_id}")
def get_job(job_id: str, user: Annotated[AuthUser, Depends(get_current_user)]):
    return serialize_job(job_id)


@app.post("/documents")
async def upload_document(
    user: Annotated[AuthUser, Depends(require_roles("uploader"))],
    file: UploadFile = File(...),
):
    data = await file.read()
    db = SessionLocal()
    try:
        doc = persist_upload(db, file.filename, data, file.content_type)
        return serialize_document(doc)
    finally:
        db.close()


@app.post("/documents/batch")
async def upload_documents_batch(
    user: Annotated[AuthUser, Depends(require_roles("uploader"))],
    files: list[UploadFile] = File(...),
):
    results = []
    db = SessionLocal()
    try:
        for file in files:
            data = await file.read()
            try:
                doc = persist_upload(db, file.filename, data, file.content_type)
                results.append({"filename": file.filename, "document": serialize_document(doc), "error": None})
            except HTTPException as exc:
                db.rollback()
                detail = str(exc.detail)
                duplicate_id = None
                if exc.status_code == 409:
                    raw_id = detail.split("id=")[-1].rstrip(")")
                    duplicate_id = int(raw_id) if raw_id.isdigit() else None
                results.append({"filename": file.filename, "document": None, "error": detail, "duplicate_id": duplicate_id})
        return {"results": results}
    finally:
        db.close()


def priority_sort_expr():
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
    if sort is None:
        return query.order_by(priority_sort_expr().asc(), Document.created_at.desc())
    if sort not in SORT_FIELDS:
        raise HTTPException(400, "unsupported sort field")
    if order not in {"asc", "desc"}:
        raise HTTPException(400, "order must be asc or desc")
    column = SORT_FIELDS[sort]
    return query.order_by(column.asc() if order == "asc" else column.desc(), Document.created_at.desc())


def paginate_documents(query, *, page: int = 1, per_page: int = 25, serializer=serialize_document):
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


@app.get("/documents")
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
    db = SessionLocal()
    try:
        query = db.query(Document).filter(Document.status != "rejected")
        query = apply_document_filters(query, q=q, filter_value=filter, priority=priority)
        query = apply_document_sort(query, sort=sort, order=order)
        return paginate_documents(query, page=page, per_page=per_page)
    finally:
        db.close()


@app.get("/review-queue")
def review_queue(
    user: Annotated[AuthUser, Depends(require_roles("reviewer"))],
    q: str | None = None,
    sort: str | None = None,
    order: str | None = None,
    page: int = 1,
    per_page: int = 25,
):
    db = SessionLocal()
    try:
        query = db.query(Document).filter(Document.status.in_(["needs_review", "failed"]))
        query = apply_document_filters(query, q=q)
        query = apply_document_sort(query, sort=sort, order=order)

        def serialize_review_doc(doc: Document):
            review_items = db.query(ReviewItem).filter_by(document_id=doc.id, status="open").order_by(ReviewItem.id).all()
            return {
                **serialize_document(doc),
                "review_item_count": len(review_items),
                "first_review_reason": review_items[0].reason if review_items else None,
            }

        return paginate_documents(query, page=page, per_page=per_page, serializer=serialize_review_doc)
    finally:
        db.close()


@app.get("/admin/failures")
def admin_failures(
    user: Annotated[AuthUser, Depends(require_roles("admin"))],
    since: str = "30d",
):
    start = parse_since(since)
    db = SessionLocal()
    try:
        examples = (
            db.query(CorrectionExample)
            .filter(CorrectionExample.created_at >= start)
            .order_by(CorrectionExample.created_at.desc(), CorrectionExample.id.desc())
            .all()
        )
        grouped: dict[str, list[CorrectionExample]] = defaultdict(list)
        trends: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for example in examples:
            grouped[example.failure_category].append(example)
            trends[example.failure_category][week_key(example.created_at)] += 1

        categories = []
        for category, rows in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
            samples = []
            for example in rows[:3]:
                doc = db.get(Document, example.document_id)
                samples.append(
                    {
                        "document_id": example.document_id,
                        "filename": doc.filename if doc else "unknown",
                        "diffs": (example.field_diffs or [])[:2],
                    }
                )
            categories.append(
                {
                    "category": category,
                    "count": len(rows),
                    "samples": samples,
                    "trend": [{"week": week, "count": count} for week, count in sorted(trends[category].items())],
                }
            )
        return {"since": since, "categories": categories}
    finally:
        db.close()


@app.get("/documents/stats")
def document_stats(user: Annotated[AuthUser, Depends(get_current_user)]):
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


@app.get("/insights/overview")
def insights_overview(
    range: str = "30d",
    user: Annotated[AuthUser, Depends(require_roles("reviewer"))] = None,
):
    if range not in ("7d", "30d", "90d", "all"):
        range = "30d"

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        range_days = {"7d": 7, "30d": 30, "90d": 90}

        if range in range_days:
            days = range_days[range]
            current_since = now - timedelta(days=days)
            prev_since = current_since - timedelta(days=days)
            prev_until = current_since
        else:
            current_since = datetime(2000, 1, 1, tzinfo=timezone.utc)
            prev_since = None
            prev_until = None

        def window_kpis(since, until):
            row = db.execute(
                text("""
                    SELECT
                        COUNT(*) FILTER (WHERE status NOT IN ('uploaded','queued','parsing')) AS processed,
                        COUNT(*) FILTER (WHERE status IN ('verified','approved')) AS good,
                        COUNT(*) FILTER (WHERE status = 'verified') AS verified,
                        AVG(confidence_score) FILTER (
                            WHERE confidence_score IS NOT NULL AND status != 'rejected'
                        ) AS mean_conf
                    FROM documents
                    WHERE created_at >= :since AND created_at < :until
                      AND status != 'rejected'
                """),
                {"since": since, "until": until},
            ).fetchone()
            if not row or not row.processed:
                return {"pass_rate": None, "mean_confidence": None, "auto_approval_rate": None}
            processed = int(row.processed)
            return {
                "pass_rate": round(int(row.good) / processed, 4),
                "mean_confidence": round(float(row.mean_conf), 4) if row.mean_conf is not None else None,
                "auto_approval_rate": round(int(row.verified) / processed, 4),
            }

        def window_median_review_hours(since, until):
            result = db.execute(
                text("""
                    SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (
                        ORDER BY EXTRACT(EPOCH FROM (al.approved_at - ri.entered_at)) / 3600
                    ) AS median_h
                    FROM (
                        SELECT document_id, MIN(created_at) AS entered_at
                        FROM review_items
                        GROUP BY document_id
                    ) ri
                    JOIN (
                        SELECT document_id, MIN(created_at) AS approved_at
                        FROM audit_log
                        WHERE action = 'approve'
                        GROUP BY document_id
                    ) al ON al.document_id = ri.document_id
                    WHERE al.approved_at >= :since AND al.approved_at < :until
                      AND al.approved_at > ri.entered_at
                """),
                {"since": since, "until": until},
            ).scalar()
            return round(float(result), 1) if result is not None else None

        curr = window_kpis(current_since, now)
        curr_review = window_median_review_hours(current_since, now)

        if prev_since is not None:
            prev = window_kpis(prev_since, prev_until)
            prev_review = window_median_review_hours(prev_since, prev_until)
        else:
            prev = {"pass_rate": None, "mean_confidence": None, "auto_approval_rate": None}
            prev_review = None

        def make_kpi(current, previous):
            delta = round(current - previous, 4) if (current is not None and previous is not None) else None
            return {"current": current, "previous": previous, "delta": delta}

        kpis = {
            "pass_rate": make_kpi(curr["pass_rate"], prev["pass_rate"]),
            "mean_confidence": make_kpi(curr["mean_confidence"], prev["mean_confidence"]),
            "median_review_time_hours": make_kpi(curr_review, prev_review),
            "auto_approval_rate": make_kpi(curr["auto_approval_rate"], prev["auto_approval_rate"]),
        }

        # --- Alerts ---
        alerts = []

        aged_cutoff = now - timedelta(hours=24)
        aged_count = int(
            db.execute(
                text("SELECT COUNT(*) FROM documents WHERE status = 'needs_review' AND created_at <= :cutoff"),
                {"cutoff": aged_cutoff},
            ).scalar()
            or 0
        )
        if aged_count > 0:
            alerts.append({
                "type": "aged_review",
                "message": f"{aged_count} doc{'s' if aged_count != 1 else ''} aged >24h in Needs Review",
                "link": "/documents?filter=needs_review",
                "severity": "warning",
            })

        def fail_rate_in_window(since, until):
            row = db.execute(
                text("""
                    SELECT COUNT(*) AS total,
                           COUNT(*) FILTER (WHERE status = 'failed') AS failed
                    FROM documents
                    WHERE created_at >= :since AND created_at < :until
                      AND status NOT IN ('uploaded','queued','parsing')
                """),
                {"since": since, "until": until},
            ).fetchone()
            if not row or not row.total:
                return None
            return int(row.failed) / int(row.total)

        week_since = now - timedelta(days=7)
        prev_week_since = week_since - timedelta(days=7)
        curr_fail = fail_rate_in_window(week_since, now)
        prev_fail = fail_rate_in_window(prev_week_since, week_since)
        if curr_fail is not None and prev_fail is not None and prev_fail > 0:
            spike = (curr_fail - prev_fail) / prev_fail
            if spike >= 0.20:
                alerts.append({
                    "type": "failure_rate_spike",
                    "message": f"Failure rate up {round(spike * 100)}% this week",
                    "link": "/admin/failures",
                    "severity": "error",
                })

        CONF_THRESHOLD = 0.70
        curr_conf = curr["mean_confidence"]
        if curr_conf is not None and curr_conf < CONF_THRESHOLD:
            alerts.append({
                "type": "low_confidence",
                "message": f"Mean confidence {round(curr_conf * 100)}% is below {round(CONF_THRESHOLD * 100)}% threshold",
                "link": "/insights/confidence",
                "severity": "warning",
            })

        # --- Volume vs confidence scatter ---
        scatter_rows = db.execute(
            text("""
                SELECT id, filename, created_at, confidence_score, status
                FROM documents
                WHERE created_at >= :since AND confidence_score IS NOT NULL AND status != 'rejected'
                ORDER BY created_at
            """),
            {"since": current_since},
        ).fetchall()
        volume_vs_confidence = [
            {
                "document_id": r.id,
                "filename": r.filename,
                "date": r.created_at.isoformat(),
                "confidence": round(float(r.confidence_score), 4),
                "status": r.status,
            }
            for r in scatter_rows
        ]

        # --- Pass rate trend ---
        trend_rows = db.execute(
            text("""
                SELECT
                    DATE(created_at AT TIME ZONE 'UTC') AS date,
                    COUNT(*) FILTER (WHERE status NOT IN ('uploaded','queued','parsing','rejected')) AS processed,
                    COUNT(*) FILTER (WHERE status IN ('verified','approved')) AS good
                FROM documents
                WHERE created_at >= :since AND status != 'rejected'
                GROUP BY DATE(created_at AT TIME ZONE 'UTC')
                ORDER BY date
            """),
            {"since": current_since},
        ).fetchall()
        pass_rate_trend = [
            {
                "date": str(r.date),
                "rate": round(int(r.good) / int(r.processed), 4) if r.processed else None,
                "count": int(r.processed) if r.processed else 0,
            }
            for r in trend_rows
            if r.processed
        ]

        # --- Failure breakdown ---
        failure_rows = db.execute(
            text("""
                SELECT failure_category, COUNT(*) AS count
                FROM correction_examples
                WHERE created_at >= :since
                GROUP BY failure_category
                ORDER BY count DESC
                LIMIT 10
            """),
            {"since": current_since},
        ).fetchall()
        failure_breakdown = [
            {
                "category": r.failure_category,
                "count": int(r.count),
                "link": f"/admin/failures?category={r.failure_category}",
            }
            for r in failure_rows
        ]

        # --- By source ---
        source_rows = db.execute(
            text("""
                SELECT
                    (j.result->>'kind') AS source,
                    COUNT(d.id) AS count,
                    AVG(d.confidence_score) AS mean_confidence,
                    SUM(CASE WHEN d.status IN ('verified','approved') THEN 1 ELSE 0 END)::float
                        / NULLIF(COUNT(d.id), 0) AS pass_rate
                FROM documents d
                JOIN (
                    SELECT DISTINCT ON (document_id) document_id, result
                    FROM parse_jobs
                    WHERE status = 'success' AND result IS NOT NULL AND result->>'kind' IS NOT NULL
                    ORDER BY document_id, finished_at DESC NULLS LAST
                ) j ON j.document_id = d.id
                WHERE d.created_at >= :since
                  AND d.status NOT IN ('uploaded','queued','parsing','rejected')
                GROUP BY j.result->>'kind'
                ORDER BY count DESC
            """),
            {"since": current_since},
        ).fetchall()
        by_source = [
            {
                "source": r.source,
                "count": int(r.count),
                "mean_confidence": round(float(r.mean_confidence), 4) if r.mean_confidence is not None else None,
                "pass_rate": round(float(r.pass_rate), 4) if r.pass_rate is not None else None,
                "mean_review_hours": None,
            }
            for r in source_rows
        ]

        # --- Queue depth (jobs queued per day) ---
        queue_rows = db.execute(
            text("""
                SELECT DATE(queued_at AT TIME ZONE 'UTC') AS date, COUNT(*) AS depth
                FROM parse_jobs
                WHERE queued_at >= :since
                GROUP BY DATE(queued_at AT TIME ZONE 'UTC')
                ORDER BY date
            """),
            {"since": current_since},
        ).fetchall()
        queue_depth = [{"date": str(r.date), "depth": int(r.depth)} for r in queue_rows]

        # --- Parse latency p50/p95/p99 (seconds) ---
        latency_rows = db.execute(
            text("""
                SELECT
                    DATE(finished_at AT TIME ZONE 'UTC') AS date,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (
                        ORDER BY EXTRACT(EPOCH FROM (finished_at - queued_at))
                    ) AS p50,
                    PERCENTILE_CONT(0.95) WITHIN GROUP (
                        ORDER BY EXTRACT(EPOCH FROM (finished_at - queued_at))
                    ) AS p95,
                    PERCENTILE_CONT(0.99) WITHIN GROUP (
                        ORDER BY EXTRACT(EPOCH FROM (finished_at - queued_at))
                    ) AS p99
                FROM parse_jobs
                WHERE status = 'success' AND finished_at IS NOT NULL AND queued_at >= :since
                GROUP BY DATE(finished_at AT TIME ZONE 'UTC')
                ORDER BY date
            """),
            {"since": current_since},
        ).fetchall()
        parse_latency = [
            {
                "date": str(r.date),
                "p50": round(float(r.p50), 1) if r.p50 is not None else None,
                "p95": round(float(r.p95), 1) if r.p95 is not None else None,
                "p99": round(float(r.p99), 1) if r.p99 is not None else None,
            }
            for r in latency_rows
        ]

        # --- Failure rate per day ---
        fail_rate_rows = db.execute(
            text("""
                SELECT
                    DATE(queued_at AT TIME ZONE 'UTC') AS date,
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE status = 'failed') AS failed
                FROM parse_jobs
                WHERE queued_at >= :since
                GROUP BY DATE(queued_at AT TIME ZONE 'UTC')
                ORDER BY date
            """),
            {"since": current_since},
        ).fetchall()
        failure_rate = [
            {
                "date": str(r.date),
                "total": int(r.total),
                "failed": int(r.failed),
                "rate": round(int(r.failed) / int(r.total), 4) if r.total else 0,
            }
            for r in fail_rate_rows
        ]

        # --- Lowest confidence (enhanced) ---
        lowest_rows = db.execute(
            text("""
                SELECT
                    d.id, d.filename, d.status, d.confidence_score,
                    d.created_at, d.account_holder, d.account_number, d.statement_period,
                    MAX(al.created_at) AS last_modified_at,
                    (ARRAY_AGG(al.actor ORDER BY al.created_at DESC))[1] AS last_modified_by
                FROM documents d
                LEFT JOIN audit_log al ON al.document_id = d.id
                WHERE d.status != 'rejected' AND d.confidence_score IS NOT NULL
                GROUP BY d.id
                ORDER BY d.confidence_score ASC
                LIMIT 20
            """),
        ).fetchall()

        lowest = []
        for r in lowest_rows:
            last_change = r.last_modified_at if r.last_modified_at else r.created_at
            if last_change.tzinfo is None:
                last_change = last_change.replace(tzinfo=timezone.utc)
            time_in_status_h = round((now - last_change).total_seconds() / 3600, 1)
            lowest.append({
                "id": r.id,
                "filename": r.filename,
                "status": r.status,
                "confidence_score": round(float(r.confidence_score), 4) if r.confidence_score else None,
                "created_at": r.created_at.isoformat(),
                "account_holder": r.account_holder,
                "account_number": r.account_number,
                "statement_period": r.statement_period,
                "priority": compute_priority(r.status, r.confidence_score),
                "time_in_status_hours": time_in_status_h,
                "last_modified_by": r.last_modified_by,
            })

        return {
            "kpis": kpis,
            "alerts": alerts,
            "volume_vs_confidence": volume_vs_confidence,
            "pass_rate_trend": pass_rate_trend,
            "failure_breakdown": failure_breakdown,
            "by_source": by_source,
            "queue_depth": queue_depth,
            "parse_latency": parse_latency,
            "failure_rate": failure_rate,
            "lowest": lowest,
        }
    finally:
        db.close()


@app.get("/documents/{doc_id}")
def get_document(doc_id: int, user: Annotated[AuthUser, Depends(get_current_user)]):
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


@app.get("/documents/{doc_id}/jobs")
def get_document_jobs(doc_id: int, user: Annotated[AuthUser, Depends(get_current_user)]):
    db = SessionLocal()
    try:
        if not db.query(Document).filter_by(id=doc_id).first():
            raise HTTPException(404, "Document not found")
        jobs = db.query(ParseJob).filter_by(document_id=doc_id).order_by(ParseJob.queued_at.desc()).all()
        return [serialize_parse_job(job) for job in jobs]
    finally:
        db.close()


@app.get("/documents/{doc_id}/versions")
def get_document_versions(doc_id: int, user: Annotated[AuthUser, Depends(require_roles("reviewer"))]):
    db = SessionLocal()
    try:
        if not db.query(Document).filter_by(id=doc_id).first():
            raise HTTPException(404, "Document not found")
        versions = db.query(DocumentVersion).filter_by(document_id=doc_id).order_by(DocumentVersion.created_at.desc()).all()
        return [serialize_document_version(version) for version in versions]
    finally:
        db.close()


@app.patch("/documents/{doc_id}/transactions/{txn_id}")
def update_transaction(
    doc_id: int,
    txn_id: int,
    payload: TransactionUpdate,
    user: Annotated[AuthUser, Depends(require_roles("reviewer"))],
):
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
        recon = recompute_review_items(db, doc, transactions)

        changed = {
            key: {"before": before[key], "after": after[key]}
            for key in ["date", "description", "amount", "type", "balance"]
            if before[key] != after[key]
        }
        save_document_version(db, doc, transactions, "review_edit", user.username)
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


@app.post("/documents/{doc_id}/approve")
def approve_document(doc_id: int, user: Annotated[AuthUser, Depends(require_roles("reviewer"))]):
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


@app.post("/documents/{doc_id}/reject")
def reject_document(
    doc_id: int,
    user: Annotated[AuthUser, Depends(require_roles("reviewer"))],
    payload: RejectRequest | None = None,
):
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


def enqueue_parse_for_document(db, doc: Document, actor: str = "system", source: str = "single"):
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


@app.post("/documents/bulk")
def bulk_documents(payload: BulkDocumentsRequest, user: Annotated[AuthUser, Depends(get_current_user)]):
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
                        {
                            "previous_status": previous_status,
                            "closed_review_items": [item.id for item in review_items],
                            "bulk": True,
                        },
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


@app.get("/documents/{doc_id}/file")
def get_document_file(
    doc_id: int,
    user: Annotated[AuthUser, Depends(get_current_user)],
    range_header: str | None = Header(default=None, alias="Range"),
):
    db = SessionLocal()
    try:
        doc = db.query(Document).filter_by(id=doc_id).first()
        if not doc:
            raise HTTPException(404, "Document not found")
        add_audit(
            db,
            doc.id,
            "view_file",
            {"range": bool(range_header), "filename": doc.filename},
            user.username,
        )
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

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers=headers,
        )
    finally:
        db.close()


@app.post("/documents/{doc_id}/extract")
def extract_document(doc_id: int, user: Annotated[AuthUser, Depends(require_roles("uploader"))]):
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


@app.post("/documents/{doc_id}/parse")
def parse_document(doc_id: int, user: Annotated[AuthUser, Depends(require_roles("uploader"))]):
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
