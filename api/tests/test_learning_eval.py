import uuid

from conftest import CorrectionExample, DocumentVersion, EvalRun, EvalSetMember, add_statement_fields, add_transactions, create_document

from app.learning import build_eval
from app.learning import compare_eval
from app.learning import eval as learning_eval


def add_versions_and_example(db, doc, category):
    transactions = add_transactions(db, doc)
    extraction = {
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
    original = DocumentVersion(document_id=doc.id, source="llm_parse", actor="worker", data={"extraction": extraction})
    corrected = DocumentVersion(document_id=doc.id, source="approval_snapshot", actor="reviewer", data={"document": extraction, "transactions": extraction["transactions"]})
    db.add_all([original, corrected])
    db.flush()
    example = CorrectionExample(
        document_id=doc.id,
        original_version_id=original.id,
        corrected_version_id=corrected.id,
        field_diffs=[] if category == "no_change" else [{"path": "transactions[0].type", "transaction_index": 0, "before": "deposit", "after": "withdrawal"}],
        failure_category=category,
        pdf_features={"bank": "Test", "layout": "tabular", "source": "digital", "page_count": 1, "transaction_count": len(transactions)},
    )
    db.add(example)
    db.commit()
    return example


def test_build_eval_creates_stratified_set(db):
    first = create_document(db, filename="pytest-eval-sign.pdf", status="approved")
    second = create_document(db, filename="pytest-eval-header.pdf", status="approved")
    add_statement_fields(first)
    add_statement_fields(second)
    add_versions_and_example(db, first, "sign_flip")
    add_versions_and_example(db, second, "header_field")

    version = f"pytest-v1-{uuid.uuid4().hex[:8]}"
    result = build_eval.build_eval_set(version, limit=30)

    assert result["count"] >= 2
    assert result["categories"]["header_field"] >= 1
    assert result["categories"]["sign_flip"] >= 1
    ids = {row.document_id for row in db.query(EvalSetMember).filter_by(eval_set_version=version).all()}
    assert {first.id, second.id} <= ids


def test_eval_records_reproducible_metrics(db, monkeypatch):
    doc = create_document(db, filename="pytest-eval-run.pdf", status="approved")
    add_statement_fields(doc)
    example = add_versions_and_example(db, doc, "no_change")
    version = f"pytest-v2-{uuid.uuid4().hex[:8]}"
    db.add(EvalSetMember(eval_set_version=version, document_id=doc.id, corrected_version_id=example.corrected_version_id))
    db.commit()

    predicted = {
        "account_holder": "Test Holder",
        "account_number": "****1234",
        "statement_period": "January 2026",
        "opening_balance": 100.0,
        "closing_balance": 125.0,
        "transactions": [
            {"date": "2026-01-01", "description": "Deposit", "amount": 50.0, "type": "deposit", "balance": 150.0},
            {"date": "2026-01-02", "description": "Withdrawal", "amount": 25.0, "type": "withdrawal", "balance": 125.0},
        ],
    }
    monkeypatch.setattr(learning_eval, "run_extraction_for_document", lambda db, doc, use_few_shot=False: (predicted, {"passed": True}, 0.94, {"recover_blank_descriptions": 1}))

    first = learning_eval.run_eval(version)
    second = learning_eval.run_eval(version)

    assert first["aggregate"] == second["aggregate"]
    assert first["aggregate"]["reconciliation_pass_rate"] == 1
    assert first["aggregate"]["field_accuracy"]["amount"] == 1
    assert first["aggregate"]["rule_corrections"]["recover_blank_descriptions"] == 1
    assert db.query(EvalRun).filter_by(eval_set_version=version).count() == 2


def test_compare_eval_reports_delta(db):
    version = f"pytest-ab-{uuid.uuid4().hex[:8]}"
    db.add(
        EvalRun(
            eval_set_version=version,
            prompt_version="prompt-v1.0",
            metrics_json={
                "few_shot": "off",
                "aggregate": {
                    "reconciliation_pass_rate": 0.5,
                    "mean_confidence": 0.4,
                    "field_accuracy": {"date": 1.0},
                },
            },
        )
    )
    db.add(
        EvalRun(
            eval_set_version=version,
            prompt_version="prompt-v1.0",
            metrics_json={
                "few_shot": "on",
                "aggregate": {
                    "reconciliation_pass_rate": 0.75,
                    "mean_confidence": 0.6,
                    "field_accuracy": {"date": 0.5},
                },
            },
        )
    )
    db.commit()

    result = compare_eval.compare(version)

    assert result["delta"]["reconciliation_pass_rate"] == 0.25
    assert result["delta"]["mean_confidence"] == 0.19999999999999996
    assert result["delta"]["field_accuracy"]["date"] == -0.5
