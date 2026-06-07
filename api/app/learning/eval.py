from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from difflib import SequenceMatcher
from statistics import mean
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.extract import classify_and_extract, render_pages_to_images
from app.learning.examples import _infer_bank, _infer_layout
from app.learning.examples import extraction_from_version
from app.learning.fewshot import retrieve_few_shot
from app.learning.rules import apply_rules
from app.llm import PROMPT_VERSION, extract_statement, extract_statement_from_images
from app.models import Document, DocumentVersion, EvalRun, EvalSetMember
from app.reconcile import correct_signs_from_balances, reconcile
from app.storage import get_object


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)


def _exact(left: Any, right: Any) -> bool:
    if isinstance(left, float) or isinstance(right, float):
        try:
            return abs(float(left) - float(right)) < 0.01
        except (TypeError, ValueError):
            return False
    return left == right


def _description_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left or "", right or "").ratio()


def compare_extractions(predicted: dict, truth: dict) -> dict:
    truth_txns = truth.get("transactions") or []
    predicted_txns = predicted.get("transactions") or []
    field_counts = {field: {"correct": 0, "total": 0} for field in ["date", "description", "amount", "type", "balance"]}
    description_scores = []

    for index, truth_txn in enumerate(truth_txns):
        predicted_txn = predicted_txns[index] if index < len(predicted_txns) else {}
        for field in field_counts:
            field_counts[field]["total"] += 1
            if field == "description":
                description_scores.append(_description_similarity(str(predicted_txn.get(field, "")), str(truth_txn.get(field, ""))))
            if _exact(predicted_txn.get(field), truth_txn.get(field)):
                field_counts[field]["correct"] += 1

    return {
        "transaction_count_match": len(predicted_txns) == len(truth_txns),
        "field_accuracy": {
            field: (counts["correct"] / counts["total"] if counts["total"] else 1.0)
            for field, counts in field_counts.items()
        },
        "mean_description_similarity": mean(description_scores) if description_scores else 1.0,
    }


def run_extraction_for_document(db, doc: Document, *, use_few_shot: bool = False) -> tuple[dict, dict, float, dict[str, int]]:
    pdf_bytes = get_object(doc.minio_key)
    extracted = classify_and_extract(pdf_bytes)
    few_shot_examples = []
    source_text = ""
    if extracted["kind"] == "digital":
        text = "\n\n".join(page["text"] for page in extracted["pages"])
        source_text = text
        if use_few_shot:
            features = {"bank": _infer_bank(text, doc), "layout": _infer_layout(text), "source": "digital", "page_count": len(extracted["pages"]), "transaction_count": 0}
            few_shot_examples = retrieve_few_shot(db, features, k=settings.few_shot_k, exclude_document_id=doc.id)
        result = extract_statement(text, few_shot_examples=few_shot_examples)
    else:
        images = render_pages_to_images(pdf_bytes)
        if use_few_shot:
            features = {"bank": "unknown", "layout": "mixed", "source": "scanned", "page_count": len(extracted["pages"]), "transaction_count": 0}
            few_shot_examples = retrieve_few_shot(db, features, k=settings.few_shot_k, exclude_document_id=doc.id)
        result = extract_statement_from_images(images, few_shot_examples=few_shot_examples)
    correct_signs_from_balances(result)
    result, rule_corrections, _ = apply_rules(result, {"text": source_text})
    recon = reconcile(result)
    recon["rule_corrections"] = rule_corrections
    return result.model_dump(), recon, 0.0, rule_corrections


def run_eval(eval_set_version: str, *, use_few_shot: bool = False) -> dict:
    db = SessionLocal()
    try:
        members = db.query(EvalSetMember).filter_by(eval_set_version=eval_set_version).order_by(EvalSetMember.id).all()
        if not members:
            raise ValueError(f"eval set {eval_set_version} has no members")
        per_doc = []
        for member in members:
            doc = db.get(Document, member.document_id)
            corrected_version = db.get(DocumentVersion, member.corrected_version_id)
            truth = extraction_from_version(corrected_version)
            predicted, recon, confidence, rule_corrections = run_extraction_for_document(db, doc, use_few_shot=use_few_shot)
            comparison = compare_extractions(predicted, truth)
            per_doc.append(
                {
                    "document_id": member.document_id,
                    "reconciliation_passed": recon["passed"],
                    "confidence": confidence,
                    "rule_corrections": rule_corrections,
                    **comparison,
                }
            )

        aggregate = {
            "documents": len(per_doc),
            "reconciliation_pass_rate": mean([1 if row["reconciliation_passed"] else 0 for row in per_doc]) if per_doc else 0,
            "mean_confidence": mean([row["confidence"] for row in per_doc]) if per_doc else 0,
            "field_accuracy": {},
        }
        for field in ["date", "description", "amount", "type", "balance"]:
            values = [row["field_accuracy"][field] for row in per_doc]
            aggregate["field_accuracy"][field] = mean(values) if values else 0

        aggregate["rule_corrections"] = {}
        for row in per_doc:
            for name, count in (row.get("rule_corrections") or {}).items():
                aggregate["rule_corrections"][name] = aggregate["rule_corrections"].get(name, 0) + count

        metrics = {
            "eval_set_version": eval_set_version,
            "prompt_version": PROMPT_VERSION,
            "few_shot": "on" if use_few_shot else "off",
            "aggregate": aggregate,
            "documents": per_doc,
        }
        db.add(EvalRun(eval_set_version=eval_set_version, prompt_version=PROMPT_VERSION, ran_at=datetime.now(timezone.utc), metrics_json=metrics))
        db.commit()
        return metrics
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--set-version", required=True)
    parser.add_argument("--few-shot", choices=["on", "off"], default="off")
    args = parser.parse_args()
    print(json.dumps(run_eval(args.set_version, use_few_shot=args.few_shot == "on"), indent=2))


if __name__ == "__main__":
    main()
