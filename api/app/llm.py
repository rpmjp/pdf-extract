import json
import urllib.request
from .config import settings
from .schemas import StatementExtraction

OLLAMA_URL = "http://host.docker.internal:11434/api/chat"

SYSTEM_PROMPT = """You extract structured data from bank statements into JSON.

EXTRACT
1. Header: account_holder, account_number, statement_period,
   opening_balance, closing_balance.
2. Every transaction.

CRITICAL RULES — follow exactly:

A) amount MUST be a positive number. Never negative. Never zero unless
   the statement literally says 0. The "type" field carries the direction:
   - type = "deposit"    -> money INTO the account (credit, payroll,
                            refund, transfer IN, mobile deposit, ACH credit)
   - type = "withdrawal" -> money OUT of the account (debit, card purchase,
                            ATM, bill pay, check, fee, transfer OUT,
                            wire OUT, ACH debit)

B) When the source text says e.g. "$58.42" for a card purchase, amount=58.42
   and type="withdrawal". DO NOT write amount=-58.42.

C) Decide type by what HAPPENED to the balance, not by which column the row
   is printed under. A "Transfer to Savings" is a withdrawal even if it
   appears under "Transfers". A "Refund posted" is a deposit. If a section
   header says "Deposits and Credits", everything inside is type="deposit"
   unless one row is explicitly described as a debit.

D) ONE TRANSACTION PER LINE/EVENT. If a sentence mentions two charges
   ("Netflix $15.99 plus a maintenance fee of $180.89"), output TWO separate
   transactions, not one combined amount.

E) balance: the running balance shown for that row.
   - If the source has a "Balance" column (tabular statements), you MUST
     extract the value from that column for every row. It is not inventing;
     the number is right there on the page.
   - Only leave balance null if no running balance is shown on/near that row
     (e.g. prose-style statements that don't display per-row balances).

F) dates in YYYY-MM-DD. opening_balance and closing_balance are the
   statement's STATED totals, never transaction amounts."""


def _chat(payload) -> StatementExtraction:
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        body = json.loads(resp.read())
    content = body["message"]["content"]
    return StatementExtraction.model_validate_json(content)


def extract_statement(text: str) -> StatementExtraction:
    return _chat({
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Extract this bank statement:\n\n{text}"},
        ],
        "stream": False,
        "format": StatementExtraction.model_json_schema(),
        "options": {"temperature": 0},
    })


def extract_statement_from_images(images_b64: list[str]) -> StatementExtraction:
    return _chat({
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",
             "content": "Extract this bank statement from the page image(s).",
             "images": images_b64},
        ],
        "stream": False,
        "format": StatementExtraction.model_json_schema(),
        "options": {"temperature": 0},
    })
