"""Pydantic schemas used at the LLM/API boundary."""
from typing import Literal
from pydantic import BaseModel, model_validator


class Transaction(BaseModel):
    """Normalized transaction shape returned by the extractor."""
    date: str
    description: str
    amount: float
    type: Literal["deposit", "withdrawal"]
    balance: float | None = None

    @model_validator(mode="after")
    def normalize_sign(self):
        """If amount comes in negative, flip it positive and infer type from the sign.
        This makes the schema robust to LLMs that copy signed values from the source."""
        if self.amount < 0:
            self.amount = abs(self.amount)
            self.type = "withdrawal"
        return self


class StatementExtraction(BaseModel):
    """Full structured representation of one bank statement."""
    account_holder: str | None = None
    account_number: str | None = None
    statement_period: str | None = None
    opening_balance: float | None = None
    closing_balance: float | None = None
    transactions: list[Transaction]
