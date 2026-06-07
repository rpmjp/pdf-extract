from conftest import (
    AuditLog,
    CorrectionExample,
    DocumentVersion,
    add_statement_fields,
    add_transactions,
    create_document,
)


def add_llm_version(db, doc, transactions):
    version = DocumentVersion(
        document_id=doc.id,
        source="llm_parse",
        actor="worker",
        data={
            "extraction": {
                "account_holder": doc.account_holder,
                "account_number": doc.account_number,
                "statement_period": doc.statement_period,
                "opening_balance": float(doc.opening_balance),
                "closing_balance": float(doc.closing_balance),
                "transactions": [
                    {
                        "date": txn.txn_date,
                        "description": txn.description,
                        "amount": float(txn.amount),
                        "type": txn.type,
                        "balance": float(txn.balance) if txn.balance is not None else None,
                    }
                    for txn in transactions
                ],
            }
        },
    )
    db.add(version)
    db.commit()
    return version


def test_approve_without_edits_writes_empty_correction_example(client, db, auth_headers, monkeypatch):
    doc = create_document(db, filename="pytest-learning-no-edit.pdf", status="needs_review")
    add_statement_fields(doc)
    transactions = add_transactions(db, doc)
    add_llm_version(db, doc, transactions)
    monkeypatch.setattr("app.learning.examples.compute_pdf_features", lambda doc, count: {"bank": "unknown", "layout": "tabular", "source": "digital", "page_count": 1, "transaction_count": count})

    response = client.post(f"/documents/{doc.id}/approve", headers=auth_headers)

    assert response.status_code == 200
    example = db.query(CorrectionExample).filter_by(document_id=doc.id).one()
    assert example.field_diffs == []
    assert example.failure_category == "no_change"
    assert db.query(AuditLog).filter_by(document_id=doc.id, action="correction_example").count() == 1


def test_approve_after_transaction_edit_records_diff(client, db, auth_headers, monkeypatch):
    doc = create_document(db, filename="pytest-learning-txn-edit.pdf", status="needs_review")
    add_statement_fields(doc)
    transactions = add_transactions(db, doc)
    add_llm_version(db, doc, transactions)
    transactions[1].type = "deposit"
    db.commit()
    monkeypatch.setattr("app.learning.examples.compute_pdf_features", lambda doc, count: {"bank": "unknown", "layout": "tabular", "source": "digital", "page_count": 1, "transaction_count": count})

    response = client.post(f"/documents/{doc.id}/approve", headers=auth_headers)

    assert response.status_code == 200
    example = db.query(CorrectionExample).filter_by(document_id=doc.id).one()
    assert example.field_diffs == [{"path": "transactions[1].type", "transaction_index": 1, "before": "withdrawal", "after": "deposit"}]
    assert example.failure_category == "sign_flip"


def test_approve_after_header_edit_records_root_diff(client, db, auth_headers, monkeypatch):
    doc = create_document(db, filename="pytest-learning-header-edit.pdf", status="needs_review")
    add_statement_fields(doc)
    transactions = add_transactions(db, doc)
    add_llm_version(db, doc, transactions)
    doc.opening_balance = 101
    db.commit()
    monkeypatch.setattr("app.learning.examples.compute_pdf_features", lambda doc, count: {"bank": "unknown", "layout": "tabular", "source": "digital", "page_count": 1, "transaction_count": count})

    response = client.post(f"/documents/{doc.id}/approve", headers=auth_headers)

    assert response.status_code == 200
    example = db.query(CorrectionExample).filter_by(document_id=doc.id).one()
    assert example.field_diffs == [{"path": "root.opening_balance", "transaction_index": None, "before": 100.0, "after": 101.0}]
    assert example.failure_category == "header_field"


def test_reapprove_does_not_duplicate_correction_example(client, db, auth_headers, monkeypatch):
    doc = create_document(db, filename="pytest-learning-idempotent.pdf", status="needs_review")
    add_statement_fields(doc)
    transactions = add_transactions(db, doc)
    add_llm_version(db, doc, transactions)
    monkeypatch.setattr("app.learning.examples.compute_pdf_features", lambda doc, count: {"bank": "unknown", "layout": "tabular", "source": "digital", "page_count": 1, "transaction_count": count})

    assert client.post(f"/documents/{doc.id}/approve", headers=auth_headers).status_code == 200
    assert client.post(f"/documents/{doc.id}/approve", headers=auth_headers).status_code == 200

    assert db.query(CorrectionExample).filter_by(document_id=doc.id).count() == 1
