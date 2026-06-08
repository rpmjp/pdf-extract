"""Statement reconciliation and transaction sign correction.

Reconciliation is the deterministic guardrail around LLM extraction.  It checks
whether transactions explain the opening-to-closing balance movement and flags
documents for review when the math does not work.
"""
from .schemas import StatementExtraction

CENTS = 0.01  # tolerance for float rounding


def reconcile(data: StatementExtraction) -> dict:
    """Verify the extracted statement is internally consistent.
    Returns checks + an overall pass/fail used for confidence routing."""
    checks = []

    deposits = sum(t.amount for t in data.transactions if t.type == "deposit")
    withdrawals = sum(t.amount for t in data.transactions if t.type == "withdrawal")

    # Check 1: statement-level balance equation
    if data.opening_balance is not None and data.closing_balance is not None:
        expected = data.opening_balance + deposits - withdrawals
        ok = abs(expected - data.closing_balance) < CENTS
        checks.append({
            "name": "statement_balance",
            "passed": ok,
            "detail": f"opening {data.opening_balance} + deposits {deposits:.2f} "
                      f"- withdrawals {withdrawals:.2f} = {expected:.2f}, "
                      f"stated closing {data.closing_balance}",
        })
    else:
        checks.append({
            "name": "statement_balance",
            "passed": False,
            "detail": "missing opening or closing balance",
        })

    # Check 2: row-level running balance continuity
    prev = data.opening_balance
    row_ok = True
    for i, t in enumerate(data.transactions):
        if t.balance is None or prev is None:
            row_ok = False
            break
        delta = t.amount if t.type == "deposit" else -t.amount
        if abs((prev + delta) - t.balance) >= CENTS:
            row_ok = False
            checks.append({
                "name": "row_balance",
                "passed": False,
                "detail": f"row {i+1} ({t.description}): {prev} {'+' if delta>=0 else '-'} "
                          f"{abs(delta):.2f} != {t.balance}",
            })
            break
        prev = t.balance
    if row_ok:
        checks.append({"name": "row_balance", "passed": True, "detail": "all rows continuous"})

    passed = all(c["passed"] for c in checks)
    return {
        "passed": passed,
        "deposits_total": round(deposits, 2),
        "withdrawals_total": round(withdrawals, 2),
        "checks": checks,
    }

def correct_signs_from_balances(data: StatementExtraction) -> int:
    """If consecutive balances are present, the direction of change is
    ground truth. Flip 'type' for any row whose stated type contradicts it.
    Returns the number of corrections made."""
    corrections = 0
    prev = data.opening_balance
    for t in data.transactions:
        if t.balance is not None and prev is not None:
            delta = round(t.balance - prev, 2)
            if delta > 0 and t.type != "deposit":
                t.type = "deposit"
                corrections += 1
            elif delta < 0 and t.type != "withdrawal":
                t.type = "withdrawal"
                corrections += 1
        if t.balance is not None:
            prev = t.balance
    return corrections
