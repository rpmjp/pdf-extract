from __future__ import annotations

import argparse
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..config import settings
from ..models import EvalRun


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)


def latest_run(db, eval_set_version: str, few_shot: str) -> EvalRun | None:
    return (
        db.query(EvalRun)
        .filter(EvalRun.eval_set_version == eval_set_version)
        .filter(EvalRun.metrics_json["few_shot"].astext == few_shot)
        .order_by(EvalRun.ran_at.desc(), EvalRun.id.desc())
        .first()
    )


def compare(eval_set_version: str) -> dict:
    db = SessionLocal()
    try:
        off = latest_run(db, eval_set_version, "off")
        on = latest_run(db, eval_set_version, "on")
        if not off or not on:
            raise ValueError(f"need both few-shot off and on eval runs for {eval_set_version}")
        off_metrics = off.metrics_json["aggregate"]
        on_metrics = on.metrics_json["aggregate"]
        return {
            "eval_set_version": eval_set_version,
            "off_run_id": off.id,
            "on_run_id": on.id,
            "few_shot_off": off_metrics,
            "few_shot_on": on_metrics,
            "delta": {
                "reconciliation_pass_rate": on_metrics["reconciliation_pass_rate"] - off_metrics["reconciliation_pass_rate"],
                "mean_confidence": on_metrics["mean_confidence"] - off_metrics["mean_confidence"],
                "field_accuracy": {
                    field: on_metrics["field_accuracy"].get(field, 0) - off_metrics["field_accuracy"].get(field, 0)
                    for field in sorted(set(off_metrics["field_accuracy"]) | set(on_metrics["field_accuracy"]))
                },
            },
        }
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--set-version", required=True)
    args = parser.parse_args()
    print(json.dumps(compare(args.set_version), indent=2))


if __name__ == "__main__":
    main()
