from decimal import Decimal


def compute_priority(status: str, confidence_score: float | Decimal | None) -> str | None:
    confidence = float(confidence_score) if confidence_score is not None else None
    if status == "failed":
        return "P1"
    if status == "needs_review" and confidence is not None and confidence < 0.6:
        return "P1"
    if status == "needs_review" and (confidence is None or confidence >= 0.6):
        return "P2"
    if status in {"verified", "approved"}:
        return "P3"
    return None
