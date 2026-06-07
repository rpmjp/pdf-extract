# Continuous Improvement

The learning layer is append-only and single-tenant for v1. Reviewer approvals create `correction_examples` that capture the original LLM output, the approval-time corrected version, field-level diffs, deterministic failure category, and PDF features.

The first eval gate is built from approved correction examples with `python -m app.learning.build_eval`. Eval runs are recorded with `python -m app.learning.eval --set-version <version> --few-shot off|on`, and A/B summaries are produced with `python -m app.learning.compare_eval --set-version <version>`.

The admin failure page lives at `/admin/failures` and requires the `admin` role. It shows category counts, weekly trends, and sample diffs linked back to source documents.

Deterministic rules live in `app.learning.rules` and run after sign correction in the worker. Rule counts are exposed in parse responses as `reconciliation.rule_corrections`.

Few-shot retrieval is feature-filtered, uses only Postgres correction examples, excludes the current document, and is disabled by default with `FEW_SHOT_ENABLED=false`. Every production worker retrieval is logged to `audit_log` as `few_shot_retrieval`.
