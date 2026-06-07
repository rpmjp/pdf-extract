from __future__ import annotations

import json
from typing import Any

from app.learning.examples import extraction_from_version
from app.models import CorrectionExample, DocumentVersion


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def truncate_extraction(extraction: dict[str, Any], max_transactions: int = 20) -> dict[str, Any]:
    copy = dict(extraction)
    copy["transactions"] = list(extraction.get("transactions") or [])[:max_transactions]
    return copy


def truncate_text(text: str, max_chars: int = 5000) -> str:
    if len(text) <= max_chars:
        return text
    lines = text.splitlines()
    header = "\n".join(lines[:12])
    remaining = "\n".join(lines[12:])
    budget = max(0, max_chars - len(header) - 40)
    return f"{header}\n...\n{remaining[:budget]}"


def _snippet_from_version(version: DocumentVersion) -> str:
    data = version.data or {}
    text = data.get("source_text")
    if text:
        return truncate_text(text)
    extraction = extraction_from_version(version)
    header = [
        f"Account holder: {extraction.get('account_holder')}",
        f"Account number: {extraction.get('account_number')}",
        f"Statement period: {extraction.get('statement_period')}",
        f"Opening balance: {extraction.get('opening_balance')}",
        f"Closing balance: {extraction.get('closing_balance')}",
    ]
    rows = [
        f"{txn.get('date')} {txn.get('description')} {txn.get('type')} {txn.get('amount')} {txn.get('balance')}"
        for txn in (extraction.get("transactions") or [])[:20]
    ]
    return "\n".join(header + rows)


def _query_candidates(db, *, bank: str | None, layout: str | None, exclude_document_id: int | None, limit: int):
    query = db.query(CorrectionExample).filter(CorrectionExample.failure_category != "no_change")
    if exclude_document_id is not None:
        query = query.filter(CorrectionExample.document_id != exclude_document_id)
    if bank:
        query = query.filter(CorrectionExample.pdf_features["bank"].astext == bank)
    if layout:
        query = query.filter(CorrectionExample.pdf_features["layout"].astext == layout)
    return query.order_by(CorrectionExample.created_at.desc(), CorrectionExample.id.desc()).limit(limit).all()


def retrieve_few_shot(db, pdf_features: dict[str, Any], *, k: int = 3, exclude_document_id: int | None = None) -> list[dict[str, Any]]:
    bank = pdf_features.get("bank")
    layout = pdf_features.get("layout")
    candidates = _query_candidates(db, bank=bank, layout=layout, exclude_document_id=exclude_document_id, limit=k)
    if len(candidates) < k:
        seen = {candidate.id for candidate in candidates}
        broader = _query_candidates(db, bank=None, layout=layout, exclude_document_id=exclude_document_id, limit=k)
        candidates.extend([candidate for candidate in broader if candidate.id not in seen])
    if len(candidates) < k:
        seen = {candidate.id for candidate in candidates}
        broader = _query_candidates(db, bank=None, layout=None, exclude_document_id=exclude_document_id, limit=k)
        candidates.extend([candidate for candidate in broader if candidate.id not in seen])

    examples = []
    for candidate in candidates[:k]:
        corrected_version = db.get(DocumentVersion, candidate.corrected_version_id)
        if not corrected_version:
            continue
        examples.append(
            {
                "correction_example_id": candidate.id,
                "document_id": candidate.document_id,
                "failure_category": candidate.failure_category,
                "text": _snippet_from_version(corrected_version),
                "extraction": truncate_extraction(extraction_from_version(corrected_version)),
            }
        )
    return examples


def build_few_shot_messages(examples: list[dict[str, Any]], *, token_budget: int) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    used = 0
    for example in examples:
        user_content = truncate_text(example["text"])
        assistant_content = json.dumps(example["extraction"], separators=(",", ":"))
        cost = estimate_tokens(user_content) + estimate_tokens(assistant_content)
        if used + cost > token_budget:
            break
        messages.append({"role": "user", "content": user_content})
        messages.append({"role": "assistant", "content": assistant_content})
        used += cost
    return messages
