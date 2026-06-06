from typing import Literal
from pydantic import BaseModel


class Transaction(BaseModel):
    date: str                       # ISO date, e.g. 2026-01-15
    description: str
    amount: float                   # always positive
    type: Literal["deposit", "withdrawal"]
    balance: float | None = None    # running balance if present


class StatementExtraction(BaseModel):
    account_holder: str | None = None
    account_number: str | None = None
    statement_period: str | None = None
    opening_balance: float | None = None
    closing_balance: float | None = None
    transactions: list[Transaction]
