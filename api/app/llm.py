import json
import urllib.request
from .config import settings
from .schemas import StatementExtraction

OLLAMA_URL = "http://host.docker.internal:11434/api/chat"

SYSTEM_PROMPT = """You extract structured data from bank statements.
Return ONLY valid JSON matching the schema. Rules:
- amount is always POSITIVE; use the type field for direction.
- type is "deposit" for money in, "withdrawal" for money out.
- Use the debit/credit column or sign to decide type.
- dates in YYYY-MM-DD format.
- Do not invent transactions. Only extract what is present."""


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
