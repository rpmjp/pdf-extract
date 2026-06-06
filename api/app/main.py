import hashlib
from fastapi import FastAPI, UploadFile, File, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import redis

from .config import settings
from .models import Document, Transaction, ReviewItem
from .storage import ensure_bucket, put_object, get_object
from .extract import classify_and_extract
from .llm import extract_statement
from .reconcile import reconcile


app = FastAPI(title="PDF Extract API")
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)


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
        doc = db.query(Document).filter_by(id=doc_id).first()
        if not doc:
            raise HTTPException(404, "Document not found")
        pdf_bytes = get_object(doc.minio_key)
        extracted = classify_and_extract(pdf_bytes)
        text = "\n\n".join(p["text"] for p in extracted["pages"])
        result = extract_statement(text)
        recon = reconcile(result)
        doc.status = "verified" if recon["passed"] else "needs_review"

        # Clear any prior rows for this doc (re-parse is idempotent)
        db.query(Transaction).filter_by(document_id=doc.id).delete()
        db.query(ReviewItem).filter_by(document_id=doc.id).delete()

        # Persist transactions
        for t in result.transactions:
            db.add(Transaction(
                document_id=doc.id,
                txn_date=t.date,
                description=t.description,
                amount=t.amount,
                type=t.type,
                balance=t.balance,
            ))

        # If it failed the gate, open a review item
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
    finally:
        db.close()