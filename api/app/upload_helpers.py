"""Upload validation, streaming, deduplication, and malware scanning helpers."""
import hashlib
import logging
import tempfile
from pathlib import Path

from fastapi import HTTPException, UploadFile

from .config import settings
from .models import Document
from .security.scanner import ScannerUnavailable, scan_document
from .storage import put_file, put_object

logger = logging.getLogger(__name__)

UPLOAD_CHUNK_SIZE = 1024 * 1024


def validate_pdf_upload(file_name: str, data: bytes, content_type: str | None = None):
    """Validate legacy in-memory uploads used by tests and helper paths."""
    if not file_name.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files accepted")
    if content_type and content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(400, "Only PDF files accepted")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(400, "Only valid PDF files accepted")
    if len(data) > settings.max_pdf_bytes:
        max_mb = settings.max_pdf_bytes // (1024 * 1024)
        raise HTTPException(413, f"PDF is too large; maximum size is {max_mb} MB")


def _scan(data: bytes, file_name: str, sha256: str) -> str:
    """Scan *data* and return the resulting scan_status string.

    Raises HTTPException(422) if malware is detected.
    Returns 'clean' or 'unscanned' (scanner unavailable).
    """
    if not settings.clamav_enabled:
        return "unscanned"

    try:
        result = scan_document(data)
        if not result.clean:
            logger.warning(
                "infected_upload_rejected filename=%r sha256=%s virus=%r",
                file_name,
                sha256,
                result.virus_name,
            )
            raise HTTPException(422, f"Malware detected: {result.virus_name or 'unknown threat'}")
        return "clean"
    except ScannerUnavailable as exc:
        logger.warning("scanner_unavailable filename=%r sha256=%s error=%s", file_name, sha256, exc)
        return "unscanned"


def persist_upload(db, file_name: str, data: bytes, content_type: str | None = None) -> Document:
    validate_pdf_upload(file_name, data, content_type)

    sha256 = hashlib.sha256(data).hexdigest()
    existing = db.query(Document).filter_by(sha256=sha256).first()
    if existing:
        raise HTTPException(409, f"Document already exists (id={existing.id})")

    scan_status = _scan(data, file_name, sha256)

    key = f"{sha256}.pdf"
    put_object(key, data)
    doc = Document(filename=file_name, sha256=sha256, minio_key=key, scan_status=scan_status)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


async def stream_upload_to_temp(file: UploadFile) -> tuple[Path, str, bytes]:
    """Stream an UploadFile to disk while hashing and enforcing the size cap.

    The endpoint never calls ``await file.read()`` for the entire PDF, which
    keeps memory bounded when users upload large statements.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files accepted")
    if file.content_type and file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(400, "Only PDF files accepted")

    digest = hashlib.sha256()
    total = 0
    first_bytes = b""
    temp = tempfile.NamedTemporaryFile(prefix="pdf-extract-", suffix=".pdf", delete=False)
    temp_path = Path(temp.name)
    try:
        with temp:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                if not first_bytes:
                    first_bytes = chunk[:5]
                total += len(chunk)
                if total > settings.max_pdf_bytes:
                    raise HTTPException(413, f"PDF is too large; maximum size is {settings.max_pdf_bytes // (1024 * 1024)} MB")
                digest.update(chunk)
                temp.write(chunk)
        if not first_bytes.startswith(b"%PDF-"):
            raise HTTPException(400, "Only valid PDF files accepted")
        return temp_path, digest.hexdigest(), first_bytes
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def persist_streamed_upload(db, file_name: str, temp_path: Path, sha256: str) -> Document:
    """Persist a streamed upload after validation/scanning and dedupe check."""
    existing = db.query(Document).filter_by(sha256=sha256).first()
    if existing:
        raise HTTPException(409, f"Document already exists (id={existing.id})")

    with open(temp_path, "rb") as fh:
        data = fh.read()

    scan_status = _scan(data, file_name, sha256)

    key = f"{sha256}.pdf"
    put_file(key, str(temp_path))
    doc = Document(filename=file_name, sha256=sha256, minio_key=key, scan_status=scan_status)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc
