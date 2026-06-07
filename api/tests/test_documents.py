from conftest import create_document, main


PDF_BYTES = b"%PDF-1.4\n%pytest\n"


def test_upload_accepts_pdf_and_rejects_duplicate(client, monkeypatch):
    stored = {}
    monkeypatch.setattr(main, "put_object", lambda key, data: stored.update({key: data}))

    response = client.post(
        "/documents",
        files={"file": ("pytest-upload.pdf", PDF_BYTES, "application/pdf")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "pytest-upload.pdf"
    assert body["status"] == "uploaded"
    assert list(stored.values()) == [PDF_BYTES]

    duplicate = client.post(
        "/documents",
        files={"file": ("pytest-upload-copy.pdf", PDF_BYTES, "application/pdf")},
    )

    assert duplicate.status_code == 409
    assert f"id={body['id']}" in duplicate.json()["detail"]


def test_upload_rejects_non_pdf(client):
    response = client.post(
        "/documents",
        files={"file": ("pytest-not-a-pdf.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Only PDF files accepted"


def test_dashboard_excludes_rejected_documents(client, db):
    visible = create_document(db, filename="pytest-visible.pdf", status="verified")
    create_document(db, filename="pytest-rejected.pdf", status="rejected")

    response = client.get("/documents")

    assert response.status_code == 200
    ids = {doc["id"] for doc in response.json()}
    assert visible.id in ids
    assert all(doc["status"] != "rejected" for doc in response.json())
