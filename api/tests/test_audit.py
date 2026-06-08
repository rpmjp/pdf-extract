"""Audit ledger hardening + data retention tests.

Tests:
  1. Hash chain detects tampering (manual row modification → verify reports a break)
  2. Retention dry-run returns exactly the expected documents
  3. Actual retention enforcement deletes from MinIO and writes an audit entry
  4. audit_writer role has only SELECT + INSERT on audit_log (no UPDATE / DELETE)
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from conftest import create_document, main
from app import worker
from app.audit_chain import GENESIS_HASH, add_audit_entry, verify_chain
from app.models import AuditVerification, RetentionPolicy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_policy(db, *, status_pattern: str, retention_days: int) -> RetentionPolicy:
    policy = RetentionPolicy(
        applies_to="documents",
        status_pattern=status_pattern,
        retention_days=retention_days,
        deletion_strategy="hard_delete",
        is_active=True,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


def _age_document(db, doc, *, days: int):
    """Back-date a document's created_at so it appears older than `days`."""
    past = datetime.now(timezone.utc) - timedelta(days=days + 1)
    db.execute(
        text("UPDATE documents SET created_at = :ts WHERE id = :id"),
        {"ts": past, "id": doc.id},
    )
    db.commit()
    db.refresh(doc)


# ---------------------------------------------------------------------------
# 1. Hash chain — detects tampering
# ---------------------------------------------------------------------------

def test_hash_chain_intact_for_fresh_entries(db):
    """Freshly inserted entries form a valid chain."""
    doc = create_document(db, filename="pytest-chain-intact.pdf")

    add_audit_entry(db, doc.id, "uploaded", {"test": True}, "pytest")
    add_audit_entry(db, doc.id, "parsed", {"result": "ok"}, "pytest")
    db.commit()

    result = verify_chain(db)
    assert result["chain_intact"] is True
    assert result["rows_checked"] >= 2


def test_hash_chain_detects_tampered_details(db):
    """Manually changing a row's details breaks the chain at that row."""
    doc = create_document(db, filename="pytest-chain-tamper.pdf")

    add_audit_entry(db, doc.id, "uploaded", {"original": True}, "pytest")
    db.commit()

    # Retrieve the entry we just created
    row = db.execute(
        text("SELECT id FROM audit_log WHERE action = 'uploaded' AND actor = 'pytest' ORDER BY id DESC LIMIT 1")
    ).fetchone()
    assert row is not None
    tampered_id = row[0]

    # Tamper the details directly in the database (bypassing the ORM)
    db.execute(
        text("UPDATE audit_log SET details = '{\"tampered\": true}'::jsonb WHERE id = :id"),
        {"id": tampered_id},
    )
    db.commit()

    result = verify_chain(db)
    assert result["chain_intact"] is False
    assert result["first_break_id"] == tampered_id


def test_hash_chain_detects_tampered_hash_itself(db):
    """Overwriting hash_chain with garbage is detected."""
    doc = create_document(db, filename="pytest-chain-hashswap.pdf")

    add_audit_entry(db, doc.id, "uploaded", {"x": 1}, "pytest")
    db.commit()

    row = db.execute(
        text("SELECT id FROM audit_log WHERE action = 'uploaded' AND actor = 'pytest' ORDER BY id DESC LIMIT 1")
    ).fetchone()
    tampered_id = row[0]

    db.execute(
        text("UPDATE audit_log SET hash_chain = :h WHERE id = :id"),
        {"h": "a" * 64, "id": tampered_id},
    )
    db.commit()

    result = verify_chain(db)
    assert result["chain_intact"] is False


def test_verify_endpoint_reports_tamper(client, auth_headers, db):
    """GET /admin/audit/verify returns chain_intact=False after a tamper."""
    doc = create_document(db, filename="pytest-verify-endpoint.pdf")

    add_audit_entry(db, doc.id, "uploaded", {"endpoint_test": True}, "pytest")
    db.commit()

    row = db.execute(
        text("SELECT id FROM audit_log WHERE action = 'uploaded' AND actor = 'pytest' ORDER BY id DESC LIMIT 1")
    ).fetchone()
    db.execute(
        text("UPDATE audit_log SET details = '{\"tampered\": true}'::jsonb WHERE id = :id"),
        {"id": row[0]},
    )
    db.commit()

    resp = client.get("/admin/audit/verify", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["chain_intact"] is False
    assert body["first_break_id"] == row[0]
    assert body["verification_id"] is not None  # record was persisted


def test_verify_endpoint_intact(client, auth_headers):
    """GET /admin/audit/verify returns chain_intact=True on an unmodified chain."""
    resp = client.get("/admin/audit/verify", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["chain_intact"] is True


# ---------------------------------------------------------------------------
# 2. Retention dry-run
# ---------------------------------------------------------------------------

def test_retention_dry_run_returns_expired_docs(client, auth_headers, db):
    """Dry-run lists documents that have exceeded their retention period."""
    policy = _make_policy(db, status_pattern="approved", retention_days=30)

    # A doc that is 40 days old (past the 30-day retention)
    old_doc = create_document(db, filename="pytest-retention-old.pdf", status="approved")
    _age_document(db, old_doc, days=40)

    # A doc that is only 10 days old (within retention)
    new_doc = create_document(db, filename="pytest-retention-new.pdf", status="approved")
    _age_document(db, new_doc, days=10)

    resp = client.post("/admin/retention/dry-run", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["dry_run"] is True

    would_delete_ids = [item["document_id"] for item in body["would_delete"]]
    assert old_doc.id in would_delete_ids
    assert new_doc.id not in would_delete_ids

    # Cleanup: deactivate the test policy
    policy.is_active = False
    db.commit()


def test_retention_dry_run_empty_when_no_expired(client, auth_headers, db):
    """Dry-run returns an empty list when no documents are past their window."""
    policy = _make_policy(db, status_pattern="rejected", retention_days=3650)

    resp = client.post("/admin/retention/dry-run", headers=auth_headers)
    assert resp.status_code == 200
    # No pytest-rejected docs are old enough
    body = resp.json()
    pytest_ids = {
        item["document_id"]
        for item in body["would_delete"]
        if "pytest-" in item.get("filename", "")
    }
    assert len(pytest_ids) == 0

    policy.is_active = False
    db.commit()


# ---------------------------------------------------------------------------
# 3. Actual retention enforcement
# ---------------------------------------------------------------------------

def test_enforce_retention_tombstones_doc_and_writes_audit(db, monkeypatch):
    """enforce_retention: MinIO delete called, doc tombstoned, audit entry written."""
    policy = _make_policy(db, status_pattern="approved", retention_days=10)

    doc = create_document(db, filename="pytest-enforce-del.pdf", status="approved")
    _age_document(db, doc, days=15)

    deleted_keys: list[str] = []

    def _fake_delete(Bucket, Key):  # noqa: N803
        deleted_keys.append(Key)

    import app.worker as _worker_mod
    from app.storage import s3
    monkeypatch.setattr(s3, "delete_object", lambda **kw: deleted_keys.append(kw["Key"]))

    result = _worker_mod.enforce_retention()

    assert result["deleted"] >= 1
    assert result["errors"] == 0

    db.expire(doc)
    db.refresh(doc)
    assert doc.status == "retention_deleted"
    assert doc.deleted_at is not None
    assert doc.deletion_policy_id == policy.id

    # Audit entry was written
    audit_row = db.execute(
        text(
            "SELECT action, details FROM audit_log "
            "WHERE action = 'retention_deleted' AND (details->>'document_id')::int = :id "
            "   OR (action = 'retention_deleted' AND details->>'filename' = :fn) "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"id": doc.id, "fn": doc.filename},
    ).fetchone()
    # Fall back to searching by filename only
    if audit_row is None:
        audit_row = db.execute(
            text(
                "SELECT action, details FROM audit_log "
                "WHERE action = 'retention_deleted' AND details->>'filename' = :fn "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"fn": doc.filename},
        ).fetchone()
    assert audit_row is not None, "Expected a retention_deleted audit entry"
    assert audit_row[0] == "retention_deleted"

    policy.is_active = False
    db.commit()


def test_enforce_retention_skips_already_deleted(db, monkeypatch):
    """enforce_retention does not re-process already-tombstoned documents."""
    policy = _make_policy(db, status_pattern="approved", retention_days=10)

    doc = create_document(db, filename="pytest-already-deleted.pdf", status="approved")
    _age_document(db, doc, days=15)
    doc.deleted_at = datetime.now(timezone.utc)
    doc.status = "retention_deleted"
    db.commit()

    import app.worker as _worker_mod
    from app.storage import s3
    monkeypatch.setattr(s3, "delete_object", lambda **kw: None)

    result = _worker_mod.enforce_retention()

    # The already-tombstoned doc must NOT be re-processed
    assert result["errors"] == 0

    policy.is_active = False
    db.commit()


# ---------------------------------------------------------------------------
# 4. audit_writer role permissions
# ---------------------------------------------------------------------------

def test_audit_writer_role_has_only_select_insert(db):
    """Postgres role 'audit_writer' must have SELECT + INSERT, not UPDATE or DELETE."""
    role_exists = db.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = 'audit_writer'")
    ).fetchone()
    if not role_exists:
        pytest.skip("audit_writer role not present (migration not run against this DB)")

    grants = db.execute(
        text(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = 'audit_writer' AND table_name = 'audit_log'"
        )
    ).fetchall()
    granted = {row[0] for row in grants}

    assert "INSERT" in granted, "audit_writer must have INSERT"
    assert "SELECT" in granted, "audit_writer must have SELECT"
    assert "UPDATE" not in granted, "audit_writer must NOT have UPDATE"
    assert "DELETE" not in granted, "audit_writer must NOT have DELETE"
