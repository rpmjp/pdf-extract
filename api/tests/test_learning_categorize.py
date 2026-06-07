from app.learning.categorize import categorize_failure


BASE = {
    "transactions": [
        {"date": "2026-01-01", "description": "Coffee", "amount": 4.25, "type": "withdrawal", "balance": 95.75},
        {"date": "2026-01-02", "description": "Payroll", "amount": 1000.0, "type": "deposit", "balance": 1095.75},
    ]
}


def diff(path, before, after, index=0):
    return {"path": path, "transaction_index": index, "before": before, "after": after}


def test_no_change():
    assert categorize_failure(BASE, []) == "no_change"


def test_sign_flip():
    assert categorize_failure(BASE, [diff("transactions[0].type", "deposit", "withdrawal")]) == "sign_flip"


def test_amount_off_by_decimal():
    assert categorize_failure(BASE, [diff("transactions[0].amount", 42.5, 4.25)]) == "amount_off_by_decimal"


def test_description_merged():
    diffs = [
        diff("transactions[0].description", "Coffee", "CoffeePayroll"),
        {"path": "transactions[1]", "transaction_index": 1, "before": BASE["transactions"][1], "after": None},
    ]
    assert categorize_failure(BASE, diffs) == "description_merged"


def test_description_split():
    assert categorize_failure(BASE, [{"path": "transactions[2]", "transaction_index": 2, "before": None, "after": BASE["transactions"][0]}]) == "description_split"


def test_date_wrong():
    assert categorize_failure(BASE, [diff("transactions[0].date", "2026-01-01", "2026-01-02")]) == "date_wrong"


def test_balance_only():
    assert categorize_failure(BASE, [diff("transactions[0].balance", 95.75, 96.75)]) == "balance_only"


def test_header_field():
    assert categorize_failure(BASE, [diff("root.opening_balance", 100, 110, None)]) == "header_field"


def test_multi():
    diffs = [
        diff("transactions[0].type", "deposit", "withdrawal"),
        diff("transactions[0].amount", 42.5, 4.25),
    ]
    assert categorize_failure(BASE, diffs) == "multi"


def test_other():
    assert categorize_failure(BASE, [diff("transactions[0].description", "Coffee", "Cafe")]) == "other"
