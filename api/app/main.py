import hashlib
import uuid
from celery.result import AsyncResult
from fastapi import FastAPI, UploadFile, File, HTTPException, Response, Header, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import redis

from .config import settings
from .models import AuditLog, Document, Transaction, ReviewItem
from .storage import ensure_bucket, put_object, get_object
from .extract import classify_and_extract, render_pages_to_images
from .llm import extract_statement, extract_statement_from_images
from .reconcile import reconcile, correct_signs_from_balances
from .schemas import StatementExtraction, Transaction as TransactionSchema
from .worker import celery_app, parse_document_task

app = FastAPI(title="PDF Extract API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Accept-Ranges", "Content-Length", "Content-Range", "Content-Disposition"],
)

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)


class TransactionUpdate(BaseModel):
    date: str
    description: str
    amount: float
    type: str
    balance: float | None = None


class RejectRequest(BaseModel):
    reason: str | None = None


ACTIVE_JOB_STATES = {"PENDING", "RECEIVED", "STARTED", "RETRY"}
RECOVERABLE_DOCUMENT_STATES = {"queued", "parsing"}
JOB_RECOVERY_LOCK_SECONDS = 30


def broker_has_pending_job(job_id: str) -> bool:
    client = redis.Redis(host=settings.redis_host, port=settings.redis_port, db=0)
    job_id_bytes = job_id.encode()
    for message in client.lrange("celery", 0, -1):
        if job_id_bytes in message:
            return True
    return False


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


def add_audit(db, doc_id: int, action: str, details: dict):
    db.add(AuditLog(document_id=doc_id, action=action, details=details, actor="reviewer"))


def recompute_review_items(db, doc: Document, transactions: list[Transaction]) -> dict:
    extraction = extraction_from_persisted(doc, transactions)
    recon = reconcile(extraction)
    recon["sign_corrections"] = 0

    db.query(ReviewItem).filter_by(document_id=doc.id, status="open").delete()
    if not recon["passed"]:
        failed = [c["detail"] for c in recon["checks"] if not c["passed"]]
        db.add(ReviewItem(document_id=doc.id, reason="; ".join(failed)))

    return recon


@app.on_event("startup")
def startup():
    ensure_bucket()


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


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    return serialize_job(job_id)


@app.post("/documents")
async def upload_document(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files accepted")

    data = await file.read()
    sha256 = hashlib.sha256(data).hexdigest()

    db = SessionLocal()
    try:
        existing = db.query(Document).filter_by(sha256=sha256).first()
        if existing:
            raise HTTPException(409, f"Document already exists (id={existing.id})")

        key = f"{sha256}.pdf"
        put_object(key, data)

        doc = Document(filename=file.filename, sha256=sha256, minio_key=key)
        db.add(doc)
        db.commit()
        db.refresh(doc)
        return {"id": doc.id, "filename": doc.filename, "sha256": sha256, "status": doc.status}
    finally:
        db.close()


@app.get("/documents")
def list_documents():
    db = SessionLocal()
    try:
        docs = db.query(Document).filter(Document.status != "rejected").order_by(Document.id.desc()).all()
        return [
            {
                "id": d.id,
                "filename": d.filename,
                "status": d.status,
                "sha256": d.sha256,
                "created_at": d.created_at.isoformat(),
            }
            for d in docs
        ]
    finally:
        db.close()


@app.get("/documents/{doc_id}")
def get_document(doc_id: int):
    db = SessionLocal()
    try:
        doc = db.query(Document).filter_by(id=doc_id).first()
        if not doc:
            raise HTTPException(404, "Document not found")

        transactions = db.query(Transaction).filter_by(document_id=doc.id).order_by(Transaction.id).all()
        review_items = db.query(ReviewItem).filter_by(document_id=doc.id).order_by(ReviewItem.id).all()
        audit_log = db.query(AuditLog).filter_by(document_id=doc.id).order_by(AuditLog.created_at.desc()).all()

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
        }
    finally:
        db.close()


@app.patch("/documents/{doc_id}/transactions/{txn_id}")
def update_transaction(doc_id: int, txn_id: int, payload: TransactionUpdate):
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
        add_audit(db, doc.id, "edit_txn", {"transaction_id": txn.id, "changes": changed, "reconciliation_passed": recon["passed"]})

        db.commit()
        db.refresh(txn)
        return serialize_transaction(txn)
    finally:
        db.close()


@app.post("/documents/{doc_id}/approve")
def approve_document(doc_id: int):
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

        add_audit(db, doc.id, "approve", {"previous_status": previous_status, "closed_review_items": [item.id for item in review_items]})
        db.commit()
        db.refresh(doc)
        return serialize_document(doc)
    finally:
        db.close()


@app.post("/documents/{doc_id}/reject")
def reject_document(doc_id: int, payload: RejectRequest | None = None):
    db = SessionLocal()
    try:
        doc = db.query(Document).filter_by(id=doc_id).first()
        if not doc:
            raise HTTPException(404, "Document not found")

        previous_status = doc.status
        doc.status = "rejected"
        reason = payload.reason if payload and payload.reason else "Rejected by reviewer"
        db.add(ReviewItem(document_id=doc.id, reason=reason, status="closed"))

        add_audit(db, doc.id, "reject", {"previous_status": previous_status, "reason": reason})
        db.commit()
        db.refresh(doc)
        return serialize_document(doc)
    finally:
        db.close()


@app.get("/documents/{doc_id}/file")
def get_document_file(doc_id: int, range_header: str | None = Header(default=None, alias="Range")):
    db = SessionLocal()
    try:
        doc = db.query(Document).filter_by(id=doc_id).first()
        if not doc:
            raise HTTPException(404, "Document not found")
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
def extract_document(doc_id: int):
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
def parse_document(doc_id: int):
    db = SessionLocal()
    try:
        doc = db.query(Document).filter_by(id=doc_id).with_for_update().first()
        if not doc:
            raise HTTPException(404, "Document not found")

        if doc.current_job_id:
            existing = AsyncResult(doc.current_job_id, app=celery_app)
            if existing.state == "PENDING" and broker_has_pending_job(doc.current_job_id):
                return serialize_job(doc.current_job_id)
            if existing.state in ACTIVE_JOB_STATES - {"PENDING"}:
                return serialize_job(doc.current_job_id)
            doc.current_job_id = None

        job_id = str(uuid.uuid4())
        doc.status = "queued"
        doc.current_job_id = job_id
        db.commit()

        parse_document_task.apply_async(args=[doc.id], task_id=job_id)
        return JSONResponse({"job_id": job_id, "status": "queued"}, status_code=status.HTTP_202_ACCEPTED)
    finally:
        db.close()
