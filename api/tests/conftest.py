import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5435")
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("REDIS_PORT", "6380")
os.environ.setdefault("MINIO_HOST", "localhost")
os.environ.setdefault("MINIO_PORT", "9000")

from app import main  # noqa: E402
from app.models import AuditLog, Document, ReviewItem, Transaction  # noqa: E402


@pytest.fixture(autouse=True)
def clean_pytest_rows():
    cleanup_pytest_rows()
    yield
    cleanup_pytest_rows()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "ensure_bucket", lambda: None)
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def db():
    session = main.SessionLocal()
    try:
        yield session
    finally:
        session.close()


def cleanup_pytest_rows():
    with main.engine.begin() as conn:
        for table in ("audit_log", "transactions", "review_items"):
            conn.execute(
                text(
                    f"""
                    DELETE FROM {table}
                    WHERE document_id IN (
                        SELECT id FROM documents WHERE filename LIKE 'pytest-%'
                    )
                    """
                )
            )
        conn.execute(text("DELETE FROM documents WHERE filename LIKE 'pytest-%'"))


def create_document(
    db,
    *,
    filename="pytest-statement.pdf",
    status="uploaded",
    current_job_id=None,
    sha256=None,
):
    doc = Document(
        filename=filename,
        sha256=sha256 or f"pytest-{filename}",
        minio_key=f"pytest/{filename}",
        status=status,
        current_job_id=current_job_id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def add_statement_fields(doc):
    doc.account_holder = "Test Holder"
    doc.account_number = "****1234"
    doc.statement_period = "January 2026"
    doc.opening_balance = 100
    doc.closing_balance = 125


def add_transactions(db, doc, *, balanced=True):
    rows = [
        Transaction(
            document_id=doc.id,
            txn_date="2026-01-01",
            description="Deposit",
            amount=50,
            type="deposit",
            balance=150,
        ),
        Transaction(
            document_id=doc.id,
            txn_date="2026-01-02",
            description="Withdrawal",
            amount=25,
            type="withdrawal",
            balance=125 if balanced else 130,
        ),
    ]
    db.add_all(rows)
    db.commit()
    return rows


__all__ = [
    "AuditLog",
    "Document",
    "ReviewItem",
    "Transaction",
    "add_statement_fields",
    "add_transactions",
    "create_document",
    "main",
]
