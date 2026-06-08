"""Deterministic post-processing rules learned from correction examples."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from ..schemas import StatementExtraction


def _amount_patterns(amount: float) -> list[str]:
    """Return regex-safe variants for matching a money amount in source text."""

    fixed = f"{amount:,.2f}"
    plain = f"{amount:.2f}"
    return [re.escape(fixed), re.escape(plain), re.escape(fixed.replace(",", ""))]


def _clean_description(line: str, txn_date: str, amount: float) -> str:
    """Strip dates, amounts, and column labels from a candidate source line."""

    text = line
    date_parts = [txn_date, txn_date.replace("-", "/")]
    for part in date_parts:
        text = text.replace(part, " ")
    text = re.sub(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", " ", text)
    for pattern in _amount_patterns(amount):
        text = re.sub(pattern, " ", text)
    text = re.sub(r"\b\d[\d,]*\.\d{2}\b", " ", text)
    text = re.sub(r"\b(debit|credit|balance|withdrawal|deposit)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" -|:$")
    return text[:160]


def recover_blank_descriptions(extraction: StatementExtraction, doc_context: dict[str, Any]) -> tuple[StatementExtraction, list[dict]]:
    """Fill blank descriptions by matching date+amount back to source text."""

    text = doc_context.get("text") or ""
    if not text:
        return extraction, []

    modified = deepcopy(extraction)
    corrections = []
    used_lines: set[int] = set()
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    for index, txn in enumerate(modified.transactions):
        if txn.description.strip():
            continue
        amount_matcher = re.compile("|".join(_amount_patterns(txn.amount)))
        for line_index, line in enumerate(lines):
            if line_index in used_lines:
                continue
            if txn.date not in line and txn.date.replace("-", "/") not in line:
                continue
            if not amount_matcher.search(line):
                continue
            description = _clean_description(line, txn.date, txn.amount)
            if description:
                txn.description = description
                used_lines.add(line_index)
                corrections.append(
                    {
                        "rule": "recover_blank_descriptions",
                        "transaction_index": index,
                        "description": description,
                    }
                )
                break

    return modified, corrections


ENABLED_RULES = [recover_blank_descriptions]


def apply_rules(extraction: StatementExtraction, doc_context: dict[str, Any]) -> tuple[StatementExtraction, dict[str, int], list[dict]]:
    """Run enabled deterministic rules and return counts for metrics/evals."""

    current = extraction
    counts: dict[str, int] = {}
    corrections: list[dict] = []
    for rule in ENABLED_RULES:
        current, rule_corrections = rule(current, doc_context)
        if rule_corrections:
            counts[rule.__name__] = len(rule_corrections)
            corrections.extend(rule_corrections)
    return current, counts, corrections
