"""
Tests for GET /documents/{id}/export

Verifies:
  - Only admin can call the endpoint
  - ZIP contains all expected files
  - manifest.json HMAC signature is valid
  - An audit entry is written for every export
  - 404 for a non-existent document
  - Transactions CSV has the expected rows
"""
import hashlib
import hmac
import io
import json
import zipfile
from datetime import datetime, timezone

import pytest

from conftest import create_document, add_transactions
from app.auth import AuthUser, create_access_token
from app.models import AuditLog, DocumentVersion, ParseJob


EXPECTED_FILES = {
    "extraction_original.json",
    "extraction_final.json",
    "transactions.csv",
    "audit_log.json",
    "versions.json",
    "corrections.json",
    "parse_jobs.json",
    "manifest.json",
}


@pytest.fixture
def admin_headers():
    token = create_access_token(AuthUser(username="pytest-admin", roles=["admin"]))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def reviewer_headers():
    token = create_access_token(AuthUser(username="pytest-reviewer", roles=["reviewer", "uploader"]))
    return {"Authorization": f"Bearer {token}"}


def _zip_from_response(response) -> zipfile.ZipFile:
    buf = io.BytesIO(response.content)
    return zipfile.ZipFile(buf)


def _manifest(zf: zipfile.ZipFile) -> dict:
    return json.loads(zf.read("manifest.json"))


def _verify_signature(manifest: dict, key: str) -> bool:
    """Re-derive the HMAC and compare — mirrors the export route's _sign_manifest."""
    body = {k: v for k, v in manifest.items() if k != "manifest_signature"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    expected = hmac.new(key.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, manifest["manifest_signature"])


# ── Access control ─────────────────────────────────────────────────────────────

def test_export_requires_admin(client, db):
    doc = create_document(db, filename="pytest-export-access.pdf")
    token = create_access_token(AuthUser(username="pytest-uploader", roles=["uploader"]))
    r = client.get(f"/documents/{doc.id}/export",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_export_reviewer_is_denied(client, db, reviewer_headers):
    doc = create_document(db, filename="pytest-export-reviewer.pdf")
    r = client.get(f"/documents/{doc.id}/export", headers=reviewer_headers)
    assert r.status_code == 403


def test_export_unauthenticated_is_denied(client, db):
    doc = create_document(db, filename="pytest-export-unauth.pdf")
    r = client.get(f"/documents/{doc.id}/export")
    assert r.status_code in (401, 403)


# ── ZIP contents ───────────────────────────────────────────────────────────────

def test_export_zip_contains_all_expected_files(client, db, admin_headers, monkeypatch):
    doc = create_document(db, filename="pytest-export-files.pdf", status="approved")
    add_transactions(db, doc)

    monkeypatch.setattr("app.routes.export.get_object", lambda key: b"%PDF-1.4 fake")

    r = client.get(f"/documents/{doc.id}/export", headers=admin_headers)
    assert r.status_code == 200
    assert "application/zip" in r.headers["content-type"]

    zf = _zip_from_response(r)
    names = set(zf.namelist())

    # PDF file is named after the document's filename
    assert doc.filename in names
    # All structured files are present
    for expected in EXPECTED_FILES:
        assert expected in names, f"Missing file in ZIP: {expected}"


def test_export_extraction_final_has_transactions(client, db, admin_headers, monkeypatch):
    doc = create_document(db, filename="pytest-export-final.pdf", status="verified")
    add_transactions(db, doc)

    monkeypatch.setattr("app.routes.export.get_object", lambda key: b"%PDF-1.4 fake")

    r = client.get(f"/documents/{doc.id}/export", headers=admin_headers)
    zf = _zip_from_response(r)
    final = json.loads(zf.read("extraction_final.json"))

    assert "transactions" in final
    assert len(final["transactions"]) == 2
    assert final["transactions"][0]["type"] in ("deposit", "withdrawal")


def test_export_transactions_csv_format(client, db, admin_headers, monkeypatch):
    doc = create_document(db, filename="pytest-export-csv.pdf", status="verified")
    add_transactions(db, doc)

    monkeypatch.setattr("app.routes.export.get_object", lambda key: b"%PDF-1.4 fake")

    r = client.get(f"/documents/{doc.id}/export", headers=admin_headers)
    zf = _zip_from_response(r)
    csv_text = zf.read("transactions.csv").decode()

    lines = [l for l in csv_text.strip().splitlines() if l]
    assert lines[0] == "date,description,amount,type,balance,confidence"
    assert len(lines) == 3  # header + 2 transactions


def test_export_extraction_original_from_llm_version(client, db, admin_headers, monkeypatch):
    doc = create_document(db, filename="pytest-export-original.pdf", status="approved")
    # Add an llm_parse version (as the worker would)
    llm_data = {"account_holder": "LLM Output", "transactions": []}
    db.add(DocumentVersion(document_id=doc.id, source="llm_parse", actor="system", data=llm_data))
    db.commit()

    monkeypatch.setattr("app.routes.export.get_object", lambda key: b"%PDF-1.4 fake")

    r = client.get(f"/documents/{doc.id}/export", headers=admin_headers)
    zf = _zip_from_response(r)
    original = json.loads(zf.read("extraction_original.json"))

    assert original.get("account_holder") == "LLM Output"


def test_export_original_empty_when_no_llm_version(client, db, admin_headers, monkeypatch):
    doc = create_document(db, filename="pytest-export-noversion.pdf", status="uploaded")
    monkeypatch.setattr("app.routes.export.get_object", lambda key: b"%PDF-1.4 fake")

    r = client.get(f"/documents/{doc.id}/export", headers=admin_headers)
    zf = _zip_from_response(r)
    original = json.loads(zf.read("extraction_original.json"))

    assert original == {}


# ── Manifest integrity ─────────────────────────────────────────────────────────

def test_export_manifest_file_hashes_match(client, db, admin_headers, monkeypatch):
    """SHA-256 of each file inside the ZIP must match the hash recorded in manifest.json."""
    doc = create_document(db, filename="pytest-export-manifest.pdf", status="approved")
    add_transactions(db, doc)
    monkeypatch.setattr("app.routes.export.get_object", lambda key: b"%PDF-1.4 fake-pdf-content")

    r = client.get(f"/documents/{doc.id}/export", headers=admin_headers)
    zf = _zip_from_response(r)
    manifest = _manifest(zf)

    for filename, recorded_hash in manifest["files"].items():
        actual_bytes = zf.read(filename)
        actual_hash = hashlib.sha256(actual_bytes).hexdigest()
        assert actual_hash == recorded_hash, (
            f"Hash mismatch for {filename}: manifest={recorded_hash}, actual={actual_hash}"
        )


def test_export_manifest_signature_valid(client, db, admin_headers, monkeypatch):
    """The HMAC signature in manifest.json must verify against the app's export key."""
    import os
    doc = create_document(db, filename="pytest-export-sig.pdf", status="approved")
    monkeypatch.setattr("app.routes.export.get_object", lambda key: b"fake")

    # The test JWT_SECRET is set in conftest env vars; export key falls back to it
    export_key = os.environ.get("JWT_SECRET", "pytest-jwt-secret-with-at-least-thirty-two-chars")

    r = client.get(f"/documents/{doc.id}/export", headers=admin_headers)
    zf = _zip_from_response(r)
    manifest = _manifest(zf)

    assert "manifest_signature" in manifest
    assert _verify_signature(manifest, export_key), "Manifest HMAC signature is invalid"


def test_export_manifest_metadata_correct(client, db, admin_headers, monkeypatch):
    doc = create_document(db, filename="pytest-export-meta.pdf", status="verified")
    monkeypatch.setattr("app.routes.export.get_object", lambda key: b"fake")

    r = client.get(f"/documents/{doc.id}/export", headers=admin_headers)
    zf = _zip_from_response(r)
    manifest = _manifest(zf)

    assert manifest["document_id"] == doc.id
    assert manifest["exported_by"] == "pytest-admin"
    assert "exported_at" in manifest


# ── Audit trail ────────────────────────────────────────────────────────────────

def test_export_writes_audit_entry(client, db, admin_headers, monkeypatch):
    doc = create_document(db, filename="pytest-export-audit.pdf", status="approved")
    monkeypatch.setattr("app.routes.export.get_object", lambda key: b"fake")

    r = client.get(f"/documents/{doc.id}/export", headers=admin_headers)
    assert r.status_code == 200

    entry = (
        db.query(AuditLog)
        .filter_by(document_id=doc.id, action="export")
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert entry is not None, "No audit entry written for export"
    assert entry.actor == "pytest-admin"
    assert entry.details.get("filename") == doc.filename


# ── Error cases ────────────────────────────────────────────────────────────────

def test_export_missing_document_returns_404(client, admin_headers):
    r = client.get("/documents/999999/export", headers=admin_headers)
    assert r.status_code == 404


def test_export_minio_unavailable_still_returns_zip(client, db, admin_headers, monkeypatch):
    """If MinIO is down the export should succeed with an empty PDF placeholder."""
    doc = create_document(db, filename="pytest-export-nominio.pdf")

    def raise_error(key):
        raise ConnectionError("MinIO unreachable")

    monkeypatch.setattr("app.routes.export.get_object", raise_error)

    r = client.get(f"/documents/{doc.id}/export", headers=admin_headers)
    assert r.status_code == 200

    zf = _zip_from_response(r)
    # The PDF entry is present but empty
    pdf_bytes = zf.read(doc.filename)
    assert pdf_bytes == b""
