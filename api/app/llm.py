import json
import urllib.request
from .config import settings
from .schemas import StatementExtraction

OLLAMA_URL = "http://host.docker.internal:11434/api/chat"

SYSTEM_PROMPT = """You extract structured data from bank statements into JSON.

Extract BOTH:
1. Account header fields (usually at the top): account_holder, account_number,
   statement_period, opening_balance, closing_balance.
2. Every transaction row in the table.

Transaction rules:
- amount is always POSITIVE; the type field carries direction.
- type is "deposit" for money in, "withdrawal" for money out.
- Decide type from the column the value sits in (Deposit vs Withdrawal),
  or from the sign if a single signed amount column is used.
- balance is the running balance shown on that row; include it if present.
- dates in YYYY-MM-DD format.
- Do not invent transactions or values. Only extract what is present.
- opening_balance and closing_balance are the statement's stated totals,
  not transaction amounts."""


def extract_statement(text: str) -> StatementExtraction:
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Extract this bank statement:\n\n{text}"},
        ],
        "stream": False,
        "format": StatementExtraction.model_json_schema(),
        "options": {"temperature": 0},
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read())
    content = body["message"]["content"]
    return StatementExtraction.model_validate_json(content)

def extract_statement_from_images(images_b64: list[str]) -> StatementExtraction:
    """Extract a statement directly from page images (for scanned PDFs)."""
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Extract this bank statement from the page image(s).",
                "images": images_b64,
            },
        ],
        "stream": False,
        "format": StatementExtraction.model_json_schema(),
        "options": {"temperature": 0},
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        body = json.loads(resp.read())
    content = body["message"]["content"]
    return StatementExtraction.model_validate_json(content)