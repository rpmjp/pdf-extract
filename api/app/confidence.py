from .schemas import StatementExtraction, Transaction


def transaction_confidence(txn: Transaction, *, document_kind: str, reconciliation_passed: bool) -> float:
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
