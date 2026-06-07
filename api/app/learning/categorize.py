from __future__ import annotations

from math import isclose
from typing import Any


DOC_FIELDS = {
    "account_holder",
    "account_number",
    "statement_period",
    "opening_balance",
    "closing_balance",
}


def _field_from_path(path: str) -> str:
    if path.startswith("root."):
        return path.split(".", 1)[1]
    if "." in path:
        return path.rsplit(".", 1)[1]
    return path


def _transaction_count(original_extraction: dict[str, Any], diffs: list[dict[str, Any]]) -> tuple[int, int]:
    original = len(original_extraction.get("transactions") or [])
    corrected = original
    for diff in diffs:
        path = str(diff.get("path", ""))
        if not path.startswith("transactions[") or "." in path:
            continue
        if diff.get("before") is None and diff.get("after") is not None:
            corrected += 1
        elif diff.get("before") is not None and diff.get("after") is None:
            corrected -= 1
    return original, corrected


def _amount_decimal_factor(before: Any, after: Any) -> bool:
    try:
        before_value = abs(float(before))
        after_value = abs(float(after))
    except (TypeError, ValueError):
        return False
    if before_value == 0 or after_value == 0:
        return False
    ratios = (10, 100, 0.1, 0.01)
    return any(isclose(after_value / before_value, ratio, rel_tol=0.005, abs_tol=0.005) for ratio in ratios)


def _has_merged_description(original_extraction: dict[str, Any], diffs: list[dict[str, Any]]) -> bool:
    original_txns = original_extraction.get("transactions") or []
    corrected_descriptions = [
        str(diff.get("after", "")).lower().replace(" ", "")
        for diff in diffs
        if str(diff.get("path", "")).endswith(".description") and diff.get("after")
    ]
    if not corrected_descriptions:
        return False
    original_descriptions = [str(txn.get("description", "")).lower().replace(" ", "") for txn in original_txns]
    for left in original_descriptions:
        for right in original_descriptions:
            if left != right and any((left + right) in desc or (right + left) in desc for desc in corrected_descriptions):
                return True
    return False


def categorize_failure(original_extraction: dict[str, Any], field_diffs: list[dict[str, Any]]) -> str:
    if not field_diffs:
        return "no_change"

    original_count, corrected_count = _transaction_count(original_extraction, field_diffs)
    fields = {_field_from_path(str(diff.get("path", ""))) for diff in field_diffs}
    categories: set[str] = set()

    if any(
        str(diff.get("path", "")).endswith(".type")
        and {diff.get("before"), diff.get("after")} == {"deposit", "withdrawal"}
        for diff in field_diffs
    ):
        categories.add("sign_flip")

    if any(str(diff.get("path", "")).endswith(".amount") and _amount_decimal_factor(diff.get("before"), diff.get("after")) for diff in field_diffs):
        categories.add("amount_off_by_decimal")

    if corrected_count < original_count and _has_merged_description(original_extraction, field_diffs):
        categories.add("description_merged")

    if corrected_count > original_count:
        categories.add("description_split")

    if fields and fields <= {"date"}:
        categories.add("date_wrong")

    if fields and fields <= {"balance"}:
        categories.add("balance_only")

    if fields and fields <= DOC_FIELDS:
        categories.add("header_field")

    if len(categories) > 1:
        return "multi"

    priority = [
        "sign_flip",
        "amount_off_by_decimal",
        "description_merged",
        "description_split",
        "date_wrong",
        "balance_only",
        "header_field",
    ]
    for category in priority:
        if category in categories:
            return category
    return "other"
