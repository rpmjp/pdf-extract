from app.learning.rules import apply_rules, recover_blank_descriptions
from app.schemas import StatementExtraction, Transaction


def test_recover_blank_description_from_matching_source_line():
    extraction = StatementExtraction(
        transactions=[
            Transaction(date="2026-06-01", description="", amount=12.34, type="withdrawal", balance=87.66),
        ]
    )
    text = "2026-06-01 Coffee Shop Purchase 12.34 87.66"

    result, corrections = recover_blank_descriptions(extraction, {"text": text})

    assert result.transactions[0].description == "Coffee Shop Purchase"
    assert corrections[0]["rule"] == "recover_blank_descriptions"


def test_recover_blank_description_does_not_modify_nonblank_or_unmatched():
    extraction = StatementExtraction(
        transactions=[
            Transaction(date="2026-06-01", description="Known", amount=12.34, type="withdrawal", balance=87.66),
            Transaction(date="2026-06-02", description="", amount=99.99, type="withdrawal", balance=1.0),
        ]
    )

    result, corrections = recover_blank_descriptions(extraction, {"text": "2026-06-01 Coffee Shop Purchase 12.34 87.66"})

    assert result.transactions[0].description == "Known"
    assert result.transactions[1].description == ""
    assert corrections == []


def test_apply_rules_returns_counts():
    extraction = StatementExtraction(transactions=[Transaction(date="2026-06-01", description="", amount=12.34, type="withdrawal", balance=87.66)])

    result, counts, corrections = apply_rules(extraction, {"text": "2026-06-01 Coffee Shop Purchase 12.34 87.66"})

    assert result.transactions[0].description
    assert counts == {"recover_blank_descriptions": 1}
    assert len(corrections) == 1
