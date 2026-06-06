import hashlib
from fastapi import FastAPI, UploadFile, File, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import redis

from .config import settings
from .models import Document
from .storage import ensure_bucket, put_object

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
