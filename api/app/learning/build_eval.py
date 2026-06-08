"""Build immutable eval sets from accumulated correction examples."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from ..config import settings
from ..models import CorrectionExample, EvalSetMember


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)


def next_version(db) -> str:
    """Return the next monotonically increasing eval-set version label."""

    versions = [row[0] for row in db.query(EvalSetMember.eval_set_version).distinct().all()]
    if not versions:
        return "v1"
    nums = [int(version[1:]) for version in versions if version.startswith("v") and version[1:].isdigit()]
    return f"v{max(nums or [0]) + 1}"


def select_examples(db, limit: int) -> list[CorrectionExample]:
    """Select a balanced sample, taking at least one row per failure category."""

    examples = db.query(CorrectionExample).order_by(CorrectionExample.created_at.desc(), CorrectionExample.id.desc()).all()
    by_category: dict[str, list[CorrectionExample]] = defaultdict(list)
    for example in examples:
        by_category[example.failure_category].append(example)

    selected: list[CorrectionExample] = []
    seen: set[int] = set()
    for category in sorted(by_category):
        example = by_category[category][0]
        selected.append(example)
        seen.add(example.id)

    remaining = [example for example in examples if example.id not in seen]
    for example in remaining:
        if len(selected) >= limit:
            break
        selected.append(example)
    return selected[:limit]


def build_eval_set(version: str | None = None, limit: int = 30) -> dict:
    """Persist a locked eval set so prompt/rule changes compare apples to apples."""

    db = SessionLocal()
    try:
        version = version or next_version(db)
        existing = db.query(EvalSetMember).filter_by(eval_set_version=version).count()
        if existing:
            raise ValueError(f"eval set {version} already exists")
        selected = select_examples(db, limit)
        if not selected:
            raise ValueError("no correction examples available; approve documents or run backfill first")
        now = datetime.now(timezone.utc)
        for example in selected:
            db.add(
                EvalSetMember(
                    eval_set_version=version,
                    document_id=example.document_id,
                    corrected_version_id=example.corrected_version_id,
                    locked_at=now,
                )
            )
        db.commit()
        categories = defaultdict(int)
        for example in selected:
            categories[example.failure_category] += 1
        return {"eval_set_version": version, "count": len(selected), "categories": dict(categories)}
    finally:
        db.close()


def main():
    """CLI entry point for creating a new eval-set version."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--version")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    print(json.dumps(build_eval_set(args.version, args.limit), indent=2))


if __name__ == "__main__":
    main()
