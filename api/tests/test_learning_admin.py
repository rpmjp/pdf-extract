from datetime import datetime, timezone

from app.auth import AuthUser, create_access_token
from conftest import CorrectionExample, DocumentVersion, add_statement_fields, add_transactions, create_document


def admin_headers():
    token = create_access_token(AuthUser(username="pytest-admin", roles=["admin", "reviewer", "uploader"]))
    return {"Authorization": f"Bearer {token}"}


def test_admin_failures_returns_counts_samples_and_trend(client, db):
    doc = create_document(db, filename="pytest-admin-failure.pdf", status="approved")
    add_statement_fields(doc)
    add_transactions(db, doc)
    original = DocumentVersion(document_id=doc.id, source="llm_parse", actor="worker", data={"extraction": {"transactions": []}})
    corrected = DocumentVersion(document_id=doc.id, source="approval_snapshot", actor="reviewer", data={"transactions": []})
    db.add_all([original, corrected])
    db.flush()
    db.add(
        CorrectionExample(
            document_id=doc.id,
            original_version_id=original.id,
            corrected_version_id=corrected.id,
            field_diffs=[{"path": "transactions[0].description", "transaction_index": 0, "before": "", "after": "Coffee"}],
            failure_category="other",
            pdf_features={"bank": "Test", "layout": "tabular", "source": "digital", "page_count": 1, "transaction_count": 1},
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    response = client.get("/admin/failures?since=30d", headers=admin_headers())

    assert response.status_code == 200
    body = response.json()
    other = next(category for category in body["categories"] if category["category"] == "other")
    assert other["count"] >= 1
    sample = next(sample for sample in other["samples"] if sample["document_id"] == doc.id)
    assert sample["diffs"][0]["path"] == "transactions[0].description"
    assert sum(point["count"] for point in other["trend"]) >= 1
