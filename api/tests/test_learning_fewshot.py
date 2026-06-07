from app.learning.fewshot import build_few_shot_messages, retrieve_few_shot
from app.llm import extract_statement
from conftest import CorrectionExample, DocumentVersion, add_statement_fields, add_transactions, create_document


def add_example(db, *, filename, bank, layout, category="sign_flip"):
    doc = create_document(db, filename=filename, status="approved")
    add_statement_fields(doc)
    add_transactions(db, doc)
    original = DocumentVersion(document_id=doc.id, source="llm_parse", actor="worker", data={"extraction": {"transactions": []}})
    corrected = DocumentVersion(
        document_id=doc.id,
        source="approval_snapshot",
        actor="reviewer",
        data={
            "document": {"account_holder": "Test Holder"},
            "transactions": [{"date": "2026-01-01", "description": "Deposit", "amount": 50, "type": "deposit", "balance": 150}],
            "source_text": "Test statement source",
        },
    )
    db.add_all([original, corrected])
    db.flush()
    example = CorrectionExample(
        document_id=doc.id,
        original_version_id=original.id,
        corrected_version_id=corrected.id,
        field_diffs=[{"path": "transactions[0].type", "transaction_index": 0, "before": "withdrawal", "after": "deposit"}],
        failure_category=category,
        pdf_features={"bank": bank, "layout": layout, "source": "digital", "page_count": 1, "transaction_count": 1},
    )
    db.add(example)
    db.commit()
    return doc, example


def test_retrieve_few_shot_filters_then_falls_back_and_excludes_current_doc(db):
    current, _ = add_example(db, filename="pytest-current-fewshot.pdf", bank="A", layout="tabular")
    strict, _ = add_example(db, filename="pytest-strict-fewshot.pdf", bank="A", layout="tabular")
    fallback, _ = add_example(db, filename="pytest-fallback-fewshot.pdf", bank="B", layout="tabular")

    examples = retrieve_few_shot(db, {"bank": "A", "layout": "tabular"}, k=2, exclude_document_id=current.id)

    ids = [example["document_id"] for example in examples]
    assert current.id not in ids
    assert strict.id in ids
    assert fallback.id in ids


def test_build_few_shot_messages_respects_budget():
    examples = [
        {"text": "short source", "extraction": {"transactions": []}},
        {"text": "x" * 20000, "extraction": {"transactions": []}},
    ]

    messages = build_few_shot_messages(examples, token_budget=100)

    assert messages == [
        {"role": "user", "content": "short source"},
        {"role": "assistant", "content": "{\"transactions\":[]}"},
    ]


def test_llm_formats_few_shot_examples(monkeypatch):
    captured = {}

    def fake_chat(payload):
        captured.update(payload)
        from app.schemas import StatementExtraction
        return StatementExtraction(transactions=[])

    monkeypatch.setattr("app.llm._chat", fake_chat)
    extract_statement(
        "New source",
        few_shot_examples=[{"text": "Old source", "extraction": {"transactions": []}}],
    )

    roles = [message["role"] for message in captured["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert captured["messages"][1]["content"] == "Old source"
    assert captured["messages"][-1]["content"].endswith("New source")
