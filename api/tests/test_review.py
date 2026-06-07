from conftest import (
    AuditLog,
    ReviewItem,
    add_statement_fields,
    add_transactions,
    create_document,
)


def test_detail_returns_doc_fields_transactions_review_and_audit(client, db, auth_headers):
    doc = create_document(db, filename="pytest-detail.pdf", status="needs_review")
    add_statement_fields(doc)
    db.add(ReviewItem(document_id=doc.id, reason="Needs a look"))
    add_transactions(db, doc)
    db.add(AuditLog(document_id=doc.id, action="seed", details={"ok": True}, actor="pytest"))
    db.commit()

    response = client.get(f"/documents/{doc.id}", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["account_holder"] == "Test Holder"
    assert body["account_number"] == "****1234"
    assert len(body["transactions"]) == 2
    assert body["review_items"][0]["reason"] == "Needs a look"
    assert body["audit_log"][0]["action"] == "seed"


def test_transaction_edit_recomputes_review_items_and_adds_audit(client, db, auth_headers):
    doc = create_document(db, filename="pytest-edit.pdf", status="needs_review")
    add_statement_fields(doc)
    transactions = add_transactions(db, doc)
    txn = transactions[1]

    response = client.patch(
        f"/documents/{doc.id}/transactions/{txn.id}",
        headers=auth_headers,
        json={
            "date": "2026-01-02",
            "description": "Withdrawal corrected",
            "amount": 25,
            "type": "withdrawal",
            "balance": 130,
        },
    )

    assert response.status_code == 200
    assert response.json()["description"] == "Withdrawal corrected"

    review_items = db.query(ReviewItem).filter_by(document_id=doc.id, status="open").all()
    audit_entries = db.query(AuditLog).filter_by(document_id=doc.id, action="edit_txn").all()
    assert len(review_items) == 1
    assert "row" in review_items[0].reason
    assert len(audit_entries) == 1
    assert audit_entries[0].details["transaction_id"] == txn.id


def test_approve_closes_review_items_and_records_audit(client, db, auth_headers):
    doc = create_document(db, filename="pytest-approve.pdf", status="needs_review")
    db.add(ReviewItem(document_id=doc.id, reason="Open issue", status="open"))
    db.commit()

    response = client.post(f"/documents/{doc.id}/approve", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert db.query(ReviewItem).filter_by(document_id=doc.id, status="open").count() == 0
    assert db.query(AuditLog).filter_by(document_id=doc.id, action="approve").count() == 1


def test_reject_hides_document_from_dashboard(client, db, auth_headers):
    doc = create_document(db, filename="pytest-reject.pdf", status="needs_review")

    response = client.post(f"/documents/{doc.id}/reject", headers=auth_headers, json={"reason": "Bad source"})

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert db.query(AuditLog).filter_by(document_id=doc.id, action="reject").count() == 1

    dashboard = client.get("/documents", headers=auth_headers)
    assert doc.id not in {row["id"] for row in dashboard.json()}


def test_review_queue_sorts_lowest_confidence_first(client, db, auth_headers):
    low = create_document(db, filename="pytest-low-confidence.pdf", status="needs_review")
    high = create_document(db, filename="pytest-high-confidence.pdf", status="needs_review")
    low.confidence_score = 0.32
    high.confidence_score = 0.88
    db.commit()

    response = client.get("/review-queue", headers=auth_headers)

    assert response.status_code == 200
    ids = [row["id"] for row in response.json()]
    assert ids.index(low.id) < ids.index(high.id)
