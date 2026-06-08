"""
Security feature tests:
  - EICAR test-virus upload is rejected with 422
  - Clean PDF passes scanning and gets scan_status='clean'
  - Scanner unavailable → upload proceeds with scan_status='unscanned'
  - Column-level encryption: account_holder / account_number round-trip
"""
from sqlalchemy import text

from conftest import create_document, main
from app import upload_helpers
from app.security.scanner import ScanResult, ScannerUnavailable

# Minimal valid PDF content used in upload tests
PDF_BYTES = b"%PDF-1.4\n%pytest-security\n"

# EICAR string wrapped in a fake PDF so it passes the magic-bytes check.
# Real AV will detect it; in tests we mock the scanner response.
EICAR_PDF = b"%PDF-" + b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


# ---------------------------------------------------------------------------
# Scan-status tests (scanner is mocked — no live ClamAV required)
# ---------------------------------------------------------------------------

def test_eicar_upload_rejected(client, auth_headers, monkeypatch):
    """ClamAV returns FOUND → 422 and the document is NOT stored."""
    monkeypatch.setattr(main.settings, "clamav_enabled", True)
    monkeypatch.setattr(
        upload_helpers,
        "scan_document",
        lambda data: ScanResult(clean=False, virus_name="Eicar-Test-Signature"),
    )

    response = client.post(
        "/documents",
        headers=auth_headers,
        files={"file": ("eicar.pdf", EICAR_PDF, "application/pdf")},
    )

    assert response.status_code == 422
    assert "Malware detected" in response.json()["detail"]


def test_clean_pdf_gets_clean_status(client, auth_headers, monkeypatch):
    """ClamAV returns OK → document stored with scan_status='clean'."""
    monkeypatch.setattr(main.settings, "clamav_enabled", True)
    monkeypatch.setattr(
        upload_helpers,
        "scan_document",
        lambda data: ScanResult(clean=True),
    )
    monkeypatch.setattr(upload_helpers, "put_file", lambda key, path: None)

    response = client.post(
        "/documents",
        headers=auth_headers,
        files={"file": ("pytest-clean.pdf", PDF_BYTES, "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["scan_status"] == "clean"


def test_scanner_unavailable_upload_proceeds(client, auth_headers, monkeypatch):
    """Scanner down → document stored with scan_status='unscanned', not rejected."""
    monkeypatch.setattr(main.settings, "clamav_enabled", True)
    monkeypatch.setattr(
        upload_helpers,
        "scan_document",
        lambda data: (_ for _ in ()).throw(ScannerUnavailable("connection refused")),
    )
    monkeypatch.setattr(upload_helpers, "put_file", lambda key, path: None)

    response = client.post(
        "/documents",
        headers=auth_headers,
        files={"file": ("pytest-scanner-down.pdf", PDF_BYTES, "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["scan_status"] == "unscanned"


def test_clamav_disabled_upload_proceeds(client, auth_headers, monkeypatch):
    """When clamav_enabled=False, upload succeeds without calling the scanner."""
    called = []
    monkeypatch.setattr(upload_helpers, "scan_document", lambda data: called.append(1) or ScanResult(clean=True))
    monkeypatch.setattr(upload_helpers, "put_file", lambda key, path: None)

    response = client.post(
        "/documents",
        headers=auth_headers,
        files={"file": ("pytest-disabled.pdf", PDF_BYTES, "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["scan_status"] == "unscanned"
    assert called == []  # scanner was never called


# ---------------------------------------------------------------------------
# Column-level encryption round-trip
# ---------------------------------------------------------------------------

def test_column_encryption_roundtrip(client, db, auth_headers):
    """account_holder and account_number are encrypted in DB but decrypted in API."""
    doc = create_document(db, filename="pytest-encrypt-roundtrip.pdf")
    doc.account_holder = "Jane Applicant"
    doc.account_number = "****5678"
    db.commit()

    # API returns decrypted plaintext
    response = client.get(f"/documents/{doc.id}", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["account_holder"] == "Jane Applicant"
    assert body["account_number"] == "****5678"

    # Raw DB value is encrypted (starts with enc:v1: prefix)
    raw_ah = db.execute(
        text(f"SELECT account_holder FROM documents WHERE id = {doc.id}")
    ).scalar()
    raw_an = db.execute(
        text(f"SELECT account_number FROM documents WHERE id = {doc.id}")
    ).scalar()

    assert raw_ah is not None and raw_ah.startswith("enc:v1:")
    assert raw_an is not None and raw_an.startswith("enc:v1:")
    assert "Jane Applicant" not in raw_ah
    assert "5678" not in raw_an
