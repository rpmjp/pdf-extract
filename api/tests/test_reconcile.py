from app.reconcile import correct_signs_from_balances, reconcile
from app.schemas import StatementExtraction, Transaction


def test_reconcile_passes_balanced_statement():
    statement = StatementExtraction(
        account_holder="Test Holder",
        opening_balance=100,
        closing_balance=125,
        transactions=[
            Transaction(date="2026-01-01", description="Deposit", amount=50, type="deposit", balance=150),
            Transaction(date="2026-01-02", description="Withdrawal", amount=25, type="withdrawal", balance=125),
        ],
    )

    result = reconcile(statement)

    assert result["passed"] is True
    assert result["deposits_total"] == 50
    assert result["withdrawals_total"] == 25
    assert all(check["passed"] for check in result["checks"])


def test_reconcile_fails_bad_running_balance():
    statement = StatementExtraction(
        opening_balance=100,
        closing_balance=125,
        transactions=[
            Transaction(date="2026-01-01", description="Deposit", amount=50, type="deposit", balance=150),
            Transaction(date="2026-01-02", description="Withdrawal", amount=25, type="withdrawal", balance=130),
        ],
    )

    result = reconcile(statement)

    assert result["passed"] is False
    assert any(check["name"] == "row_balance" and not check["passed"] for check in result["checks"])


def test_correct_signs_uses_balances_as_ground_truth():
    statement = StatementExtraction(
        opening_balance=100,
        closing_balance=125,
        transactions=[
            Transaction(date="2026-01-01", description="Actually deposit", amount=50, type="withdrawal", balance=150),
            Transaction(date="2026-01-02", description="Actually withdrawal", amount=25, type="deposit", balance=125),
        ],
    )

    corrections = correct_signs_from_balances(statement)

    assert corrections == 2
    assert [txn.type for txn in statement.transactions] == ["deposit", "withdrawal"]
