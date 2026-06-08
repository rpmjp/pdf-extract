from conftest import AuditLog, ParseJob, ReviewItem, create_document, main
from app.auth import AuthUser, create_access_token
from app import celery_helpers, upload_helpers
from app.routes import documents as documents_router


PDF_BYTES = b"%PDF-1.4\n%pytest\n"


def test_upload_accepts_pdf_and_rejects_duplicate(client, auth_headers, monkeypatch):
    stored = {}
    monkeypatch.setattr(upload_helpers, "put_file", lambda key, path: stored.update({key: path}))

    response = client.post(
        "/documents",
        headers=auth_headers,
        files={"file": ("pytest-upload.pdf", PDF_BYTES, "application/pdf")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "pytest-upload.pdf"
    assert body["status"] == "uploaded"
    assert list(stored.keys()) == [f"{body['sha256']}.pdf"]

    duplicate = client.post(
        "/documents",
        headers=auth_headers,
        files={"file": ("pytest-upload-copy.pdf", PDF_BYTES, "application/pdf")},
    )

    assert duplicate.status_code == 409
    assert f"id={body['id']}" in duplicate.json()["detail"]


def test_upload_rejects_non_pdf(client, auth_headers):
    response = client.post(
        "/documents",
        headers=auth_headers,
        files={"file": ("pytest-not-a-pdf.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Only PDF files accepted"


def test_upload_rejects_pdf_extension_with_invalid_content(client, auth_headers):
    response = client.post(
        "/documents",
        headers=auth_headers,
        files={"file": ("pytest-fake.pdf", b"not a pdf", "application/pdf")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Only valid PDF files accepted"


def test_upload_rejects_oversized_pdf(client, auth_headers, monkeypatch):
    monkeypatch.setattr(main.settings, "max_pdf_bytes", 8)

    response = client.post(
        "/documents",
        headers=auth_headers,
        files={"file": ("pytest-huge.pdf", b"%PDF-1.4\npayload", "application/pdf")},
    )

    assert response.status_code == 413
    assert "too large" in response.json()["detail"]


def test_streaming_upload_accepts_10mb_pdf(client, auth_headers, monkeypatch):
    monkeypatch.setattr(upload_helpers, "put_file", lambda key, path: None)
    data = b"%PDF-" + (b"x" * (10 * 1024 * 1024))

    response = client.post(
        "/documents",
        headers=auth_headers,
        files={"file": ("pytest-10mb.pdf", data, "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["filename"] == "pytest-10mb.pdf"


def test_streaming_upload_rejects_60mb_pdf(client, auth_headers):
    data = b"%PDF-" + (b"x" * (60 * 1024 * 1024))

    response = client.post(
        "/documents",
        headers=auth_headers,
        files={"file": ("pytest-60mb.pdf", data, "application/pdf")},
    )

    assert response.status_code == 413


def test_dashboard_excludes_rejected_documents(client, db, auth_headers):
    visible = create_document(db, filename="pytest-visible.pdf", status="verified")
    create_document(db, filename="pytest-rejected.pdf", status="rejected")

    response = client.get("/documents", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    ids = {doc["id"] for doc in body["items"]}
    assert visible.id in ids
    assert all(doc["status"] != "rejected" for doc in body["items"])


def test_documents_query_params_combine(client, db, auth_headers):
    first = create_document(db, filename="pytest-robert-low.pdf", status="needs_review")
    second = create_document(db, filename="pytest-robert-high.pdf", status="needs_review")
    create_document(db, filename="pytest-robert-processing.pdf", status="queued")
    create_document(db, filename="pytest-other.pdf", status="needs_review")
    first.account_holder = "Robert Pierre"
    second.account_holder = "Robert Pierre"
    first.confidence_score = 0.45
    second.confidence_score = 0.8
    db.commit()

    response = client.get(
        "/documents?q=robert&sort=confidence_score&order=asc&filter=needs_review&page=1&per_page=50",
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    ids = [row["id"] for row in body["items"]]
    assert ids == [first.id, second.id]
    assert body["total"] == 2
    assert body["page"] == 1
    assert body["per_page"] == 50


def test_batch_upload_returns_success_and_file_errors(client, auth_headers, monkeypatch):
    monkeypatch.setattr(upload_helpers, "put_file", lambda key, path: None)

    response = client.post(
        "/documents/batch",
        headers=auth_headers,
        files=[
            ("files", ("pytest-batch-a.pdf", b"%PDF-a", "application/pdf")),
            ("files", ("pytest-batch-b.txt", b"nope", "text/plain")),
        ],
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert results[0]["document"]["filename"] == "pytest-batch-a.pdf"
    assert results[1]["error"] == "Only PDF files accepted"


def test_bulk_reparse_success_and_partial_failure(client, db, auth_headers, monkeypatch):
    queued_calls = []
    monkeypatch.setattr(celery_helpers.parse_document_task, "apply_async", lambda *args, **kwargs: queued_calls.append((args, kwargs)))
    ok = create_document(db, filename="pytest-bulk-reparse-ok.pdf", status="verified")
    busy = create_document(db, filename="pytest-bulk-reparse-busy.pdf", status="parsing")

    response = client.post(
        "/documents/bulk",
        headers=auth_headers,
        json={"action": "reparse", "document_ids": [ok.id, busy.id]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["succeeded"] == [ok.id]
    assert body["failed"][0]["id"] == busy.id
    assert "already processing" in body["failed"][0]["error"]
    assert db.query(ParseJob).filter_by(document_id=ok.id, status="queued").count() == 1
    audit = db.query(AuditLog).filter_by(document_id=ok.id, action="parse_queued").one()
    assert audit.actor == "pytest-reviewer"
    assert audit.details["source"] == "bulk"
    assert len(queued_calls) == 1


def test_bulk_approve_success_and_partial_failure(client, db, auth_headers, monkeypatch):
    monkeypatch.setattr(documents_router, "create_correction_example_for_document", lambda db, doc, actor: None)
    ok = create_document(db, filename="pytest-bulk-approve-ok.pdf", status="verified")
    not_verified = create_document(db, filename="pytest-bulk-approve-bad.pdf", status="needs_review")
    db.add(ReviewItem(document_id=ok.id, reason="Open", status="open"))
    db.commit()

    response = client.post(
        "/documents/bulk",
        headers=auth_headers,
        json={"action": "approve", "document_ids": [ok.id, not_verified.id]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["succeeded"] == [ok.id]
    assert body["failed"][0]["id"] == not_verified.id
    db.refresh(ok)
    assert ok.status == "approved"
    assert db.query(ReviewItem).filter_by(document_id=ok.id, status="open").count() == 0
    assert db.query(AuditLog).filter_by(document_id=ok.id, action="approve").count() == 1


def test_bulk_reject_success_and_partial_failure(client, db, auth_headers):
    ok = create_document(db, filename="pytest-bulk-reject-ok.pdf", status="failed")
    bad = create_document(db, filename="pytest-bulk-reject-bad.pdf", status="verified")

    response = client.post(
        "/documents/bulk",
        headers=auth_headers,
        json={"action": "reject", "document_ids": [ok.id, bad.id], "reason": "Duplicate source"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["succeeded"] == [ok.id]
    assert body["failed"][0]["id"] == bad.id
    db.refresh(ok)
    assert ok.status == "rejected"
    assert db.query(ReviewItem).filter_by(document_id=ok.id, status="closed").first().reason == "Duplicate source"


def test_filter_needs_review_returns_only_matching_docs(client, db, auth_headers):
    nr = create_document(db, filename="pytest-filt-nr.pdf", status="needs_review")
    failed = create_document(db, filename="pytest-filt-failed.pdf", status="failed")
    approved = create_document(db, filename="pytest-filt-approved.pdf", status="approved")

    response = client.get("/documents?filter=needs_review&per_page=100", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    ids = {doc["id"] for doc in body["items"]}
    assert nr.id in ids
    assert failed.id in ids
    assert approved.id not in ids
    assert all(doc["status"] in ("needs_review", "failed") for doc in body["items"] if doc["id"] in {nr.id, failed.id})


def test_filter_processing_returns_only_matching_docs(client, db, auth_headers):
    queued = create_document(db, filename="pytest-filt-queued.pdf", status="queued")
    parsing = create_document(db, filename="pytest-filt-parsing.pdf", status="parsing")
    verified = create_document(db, filename="pytest-filt-verified.pdf", status="verified")

    response = client.get("/documents?filter=processing&per_page=100", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    ids = {doc["id"] for doc in body["items"]}
    assert queued.id in ids
    assert parsing.id in ids
    assert verified.id not in ids


def test_no_filter_returns_all_non_rejected_docs(client, db, auth_headers):
    visible = create_document(db, filename="pytest-nofilt-visible.pdf", status="verified")
    rejected = create_document(db, filename="pytest-nofilt-rejected.pdf", status="rejected")

    response = client.get("/documents?per_page=100", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    ids = {doc["id"] for doc in body["items"]}
    assert visible.id in ids
    assert rejected.id not in ids
    assert all(doc["status"] != "rejected" for doc in body["items"])


def test_bulk_actions_enforce_roles(client, db):
    doc = create_document(db, filename="pytest-bulk-role.pdf", status="verified")
    uploader_token = create_access_token(AuthUser(username="pytest-uploader", roles=["uploader"]))
    reviewer_token = create_access_token(AuthUser(username="pytest-reviewer-only", roles=["reviewer"]))

    approve = client.post(
        "/documents/bulk",
        headers={"Authorization": f"Bearer {uploader_token}"},
        json={"action": "approve", "document_ids": [doc.id]},
    )
    reparse = client.post(
        "/documents/bulk",
        headers={"Authorization": f"Bearer {reviewer_token}"},
        json={"action": "reparse", "document_ids": [doc.id]},
    )

    assert approve.status_code == 403
    assert reparse.status_code == 403


def test_get_document_file_records_view_audit(client, db, auth_headers, monkeypatch):
    doc = create_document(db, filename="pytest-view-file.pdf", status="verified")
    monkeypatch.setattr(documents_router, "get_object", lambda key: PDF_BYTES)

    response = client.get(f"/documents/{doc.id}/file", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    audit = db.query(AuditLog).filter_by(document_id=doc.id, action="view_file").one()
    assert audit.actor == "pytest-reviewer"
    assert audit.details["filename"] == "pytest-view-file.pdf"
