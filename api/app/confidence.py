"""Deterministic confidence scoring for extracted statement data.

LLMs are poor at calibrated self-confidence, so this module scores extraction
quality from observable signals: source type, missing fields, reconciliation
status, and disagreement between two extraction passes.
"""
from .schemas import StatementExtraction, Transaction


def transaction_confidence(txn: Transaction, *, document_kind: str, reconciliation_passed: bool) -> float:
    """Score one transaction using deterministic quality heuristics."""
    score = 0.94 if reconciliation_passed else 0.72
    if document_kind == "scanned":
        score -= 0.12
    if txn.balance is None:
        score -= 0.18
    if not txn.description.strip():
        score -= 0.12
    if txn.amount == 0:
        score -= 0.08
    return round(max(0.05, min(score, 0.99)), 2)


def document_confidence(extraction: StatementExtraction, *, document_kind: str, reconciliation_passed: bool) -> float:
    """Score a document by its weakest transaction and required header fields."""
    if not extraction.transactions:
        return 0.05
    scores = [
        transaction_confidence(txn, document_kind=document_kind, reconciliation_passed=reconciliation_passed)
        for txn in extraction.transactions
    ]
    doc_score = min(scores)
    if extraction.opening_balance is None or extraction.closing_balance is None:
        doc_score -= 0.15
    return round(max(0.05, min(doc_score, 0.99)), 2)


def extraction_disagreement(primary: StatementExtraction, secondary: StatementExtraction) -> dict:
    """Compare two extraction passes and return field-level disagreements."""
    checks = []

    for field in ("account_holder", "account_number", "statement_period", "opening_balance", "closing_balance"):
        left = getattr(primary, field)
        right = getattr(secondary, field)
        if left != right:
            checks.append({"field": field, "primary": left, "secondary": right})

    if len(primary.transactions) != len(secondary.transactions):
        checks.append(
            {
                "field": "transactions.count",
                "primary": len(primary.transactions),
                "secondary": len(secondary.transactions),
            }
        )

    for index, (left, right) in enumerate(zip(primary.transactions, secondary.transactions), start=1):
        for field in ("date", "description", "amount", "type", "balance"):
            left_value = getattr(left, field)
            right_value = getattr(right, field)
            if isinstance(left_value, float) or isinstance(right_value, float):
                if left_value is None or right_value is None:
                    different = left_value != right_value
                else:
                    different = abs(float(left_value) - float(right_value)) >= 0.01
            else:
                different = left_value != right_value
            if different:
                checks.append(
                    {
                        "field": f"transactions[{index}].{field}",
                        "primary": left_value,
                        "secondary": right_value,
                    }
                )

    compared = 5 + max(len(primary.transactions), len(secondary.transactions)) * 5
    score = min(1.0, len(checks) / max(1, compared))
    return {"score": round(score, 2), "checks": checks}


def apply_disagreement_penalty(confidence: float, disagreement: dict) -> float:
    """Lower confidence proportionally when ensemble extraction disagrees."""
    penalty = min(0.35, disagreement["score"] * 0.7)
    return round(max(0.05, confidence - penalty), 2)
