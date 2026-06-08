"""Operational analytics routes.

Insights endpoints intentionally compute dashboard data from existing documents,
jobs, audit logs, and correction examples. They do not create new business
state; their purpose is to expose quality trends and reviewer risk signals.
"""

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text

from ..auth import AuthUser, require_roles
from ..db import SessionLocal
from ..priority import compute_priority

router = APIRouter(tags=["Insights"])


@router.get("/insights/overview")
def insights_overview(
    range: str = "30d",
    user: Annotated[AuthUser, Depends(require_roles("reviewer"))] = None,
):
    """Return KPI, alert, trend, and low-confidence document data."""

    if range not in ("7d", "30d", "90d", "all"):
        range = "30d"

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        range_days = {"7d": 7, "30d": 30, "90d": 90}

        if range in range_days:
            days = range_days[range]
            current_since = now - timedelta(days=days)
            prev_since = current_since - timedelta(days=days)
            prev_until = current_since
        else:
            current_since = datetime(2000, 1, 1, tzinfo=timezone.utc)
            prev_since = None
            prev_until = None

        def window_kpis(since, until):
            """Compute pass/confidence/auto-approval metrics for a time window."""

            row = db.execute(
                text("""
                    SELECT
                        COUNT(*) FILTER (WHERE status NOT IN ('uploaded','queued','parsing')) AS processed,
                        COUNT(*) FILTER (WHERE status IN ('verified','approved')) AS good,
                        COUNT(*) FILTER (WHERE status = 'verified') AS verified,
                        AVG(confidence_score) FILTER (
                            WHERE confidence_score IS NOT NULL AND status != 'rejected'
                        ) AS mean_conf
                    FROM documents
                    WHERE created_at >= :since AND created_at < :until
                      AND status != 'rejected'
                """),
                {"since": since, "until": until},
            ).fetchone()
            if not row or not row.processed:
                return {"pass_rate": None, "mean_confidence": None, "auto_approval_rate": None}
            processed = int(row.processed)
            return {
                "pass_rate": round(int(row.good) / processed, 4),
                "mean_confidence": round(float(row.mean_conf), 4) if row.mean_conf is not None else None,
                "auto_approval_rate": round(int(row.verified) / processed, 4),
            }

        def window_median_review_hours(since, until):
            """Measure reviewer turnaround from first review item to approval."""

            result = db.execute(
                text("""
                    SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (
                        ORDER BY EXTRACT(EPOCH FROM (al.approved_at - ri.entered_at)) / 3600
                    ) AS median_h
                    FROM (
                        SELECT document_id, MIN(created_at) AS entered_at
                        FROM review_items
                        GROUP BY document_id
                    ) ri
                    JOIN (
                        SELECT document_id, MIN(created_at) AS approved_at
                        FROM audit_log
                        WHERE action = 'approve'
                        GROUP BY document_id
                    ) al ON al.document_id = ri.document_id
                    WHERE al.approved_at >= :since AND al.approved_at < :until
                      AND al.approved_at > ri.entered_at
                """),
                {"since": since, "until": until},
            ).scalar()
            return round(float(result), 1) if result is not None else None

        curr = window_kpis(current_since, now)
        curr_review = window_median_review_hours(current_since, now)

        if prev_since is not None:
            prev = window_kpis(prev_since, prev_until)
            prev_review = window_median_review_hours(prev_since, prev_until)
        else:
            prev = {"pass_rate": None, "mean_confidence": None, "auto_approval_rate": None}
            prev_review = None

        def make_kpi(current, previous):
            """Package a KPI with its previous-window comparison."""

            delta = round(current - previous, 4) if (current is not None and previous is not None) else None
            return {"current": current, "previous": previous, "delta": delta}

        kpis = {
            "pass_rate": make_kpi(curr["pass_rate"], prev["pass_rate"]),
            "mean_confidence": make_kpi(curr["mean_confidence"], prev["mean_confidence"]),
            "median_review_time_hours": make_kpi(curr_review, prev_review),
            "auto_approval_rate": make_kpi(curr["auto_approval_rate"], prev["auto_approval_rate"]),
        }

        alerts = []
        aged_cutoff = now - timedelta(hours=24)
        aged_count = int(
            db.execute(
                text("SELECT COUNT(*) FROM documents WHERE status = 'needs_review' AND created_at <= :cutoff"),
                {"cutoff": aged_cutoff},
            ).scalar()
            or 0
        )
        if aged_count > 0:
            alerts.append({
                "type": "aged_review",
                "message": f"{aged_count} doc{'s' if aged_count != 1 else ''} aged >24h in Needs Review",
                "link": "/documents?filter=needs_review",
                "severity": "warning",
            })

        def fail_rate_in_window(since, until):
            """Return failed-job share for alert spike detection."""

            row = db.execute(
                text("""
                    SELECT COUNT(*) AS total,
                           COUNT(*) FILTER (WHERE status = 'failed') AS failed
                    FROM documents
                    WHERE created_at >= :since AND created_at < :until
                      AND status NOT IN ('uploaded','queued','parsing')
                """),
                {"since": since, "until": until},
            ).fetchone()
            if not row or not row.total:
                return None
            return int(row.failed) / int(row.total)

        week_since = now - timedelta(days=7)
        prev_week_since = week_since - timedelta(days=7)
        curr_fail = fail_rate_in_window(week_since, now)
        prev_fail = fail_rate_in_window(prev_week_since, week_since)
        if curr_fail is not None and prev_fail is not None and prev_fail > 0:
            spike = (curr_fail - prev_fail) / prev_fail
            if spike >= 0.20:
                alerts.append({
                    "type": "failure_rate_spike",
                    "message": f"Failure rate up {round(spike * 100)}% this week",
                    "link": "/admin/failures",
                    "severity": "error",
                })

        CONF_THRESHOLD = 0.70
        curr_conf = curr["mean_confidence"]
        if curr_conf is not None and curr_conf < CONF_THRESHOLD:
            alerts.append({
                "type": "low_confidence",
                "message": f"Mean confidence {round(curr_conf * 100)}% is below {round(CONF_THRESHOLD * 100)}% threshold",
                "link": "/insights/confidence",
                "severity": "warning",
            })

        scatter_rows = db.execute(
            text("""
                SELECT id, filename, created_at, confidence_score, status
                FROM documents
                WHERE created_at >= :since AND confidence_score IS NOT NULL AND status != 'rejected'
                ORDER BY created_at
            """),
            {"since": current_since},
        ).fetchall()
        volume_vs_confidence = [
            {
                "document_id": r.id,
                "filename": r.filename,
                "date": r.created_at.isoformat(),
                "confidence": round(float(r.confidence_score), 4),
                "status": r.status,
            }
            for r in scatter_rows
        ]

        trend_rows = db.execute(
            text("""
                SELECT
                    DATE(created_at AT TIME ZONE 'UTC') AS date,
                    COUNT(*) FILTER (WHERE status NOT IN ('uploaded','queued','parsing','rejected')) AS processed,
                    COUNT(*) FILTER (WHERE status IN ('verified','approved')) AS good
                FROM documents
                WHERE created_at >= :since AND status != 'rejected'
                GROUP BY DATE(created_at AT TIME ZONE 'UTC')
                ORDER BY date
            """),
            {"since": current_since},
        ).fetchall()
        pass_rate_trend = [
            {
                "date": str(r.date),
                "rate": round(int(r.good) / int(r.processed), 4) if r.processed else None,
                "count": int(r.processed) if r.processed else 0,
            }
            for r in trend_rows
            if r.processed
        ]

        failure_rows = db.execute(
            text("""
                SELECT failure_category, COUNT(*) AS count
                FROM correction_examples
                WHERE created_at >= :since
                GROUP BY failure_category
                ORDER BY count DESC
                LIMIT 10
            """),
            {"since": current_since},
        ).fetchall()
        failure_breakdown = [
            {
                "category": r.failure_category,
                "count": int(r.count),
                "link": f"/admin/failures?category={r.failure_category}",
            }
            for r in failure_rows
        ]

        source_rows = db.execute(
            text("""
                SELECT
                    (j.result->>'kind') AS source,
                    COUNT(d.id) AS count,
                    AVG(d.confidence_score) AS mean_confidence,
                    SUM(CASE WHEN d.status IN ('verified','approved') THEN 1 ELSE 0 END)::float
                        / NULLIF(COUNT(d.id), 0) AS pass_rate
                FROM documents d
                JOIN (
                    SELECT DISTINCT ON (document_id) document_id, result
                    FROM parse_jobs
                    WHERE status = 'success' AND result IS NOT NULL AND result->>'kind' IS NOT NULL
                    ORDER BY document_id, finished_at DESC NULLS LAST
                ) j ON j.document_id = d.id
                WHERE d.created_at >= :since
                  AND d.status NOT IN ('uploaded','queued','parsing','rejected')
                GROUP BY j.result->>'kind'
                ORDER BY count DESC
            """),
            {"since": current_since},
        ).fetchall()
        by_source = [
            {
                "source": r.source,
                "count": int(r.count),
                "mean_confidence": round(float(r.mean_confidence), 4) if r.mean_confidence is not None else None,
                "pass_rate": round(float(r.pass_rate), 4) if r.pass_rate is not None else None,
                "mean_review_hours": None,
            }
            for r in source_rows
        ]

        queue_rows = db.execute(
            text("""
                SELECT DATE(queued_at AT TIME ZONE 'UTC') AS date, COUNT(*) AS depth
                FROM parse_jobs
                WHERE queued_at >= :since
                GROUP BY DATE(queued_at AT TIME ZONE 'UTC')
                ORDER BY date
            """),
            {"since": current_since},
        ).fetchall()
        queue_depth = [{"date": str(r.date), "depth": int(r.depth)} for r in queue_rows]

        latency_rows = db.execute(
            text("""
                SELECT
                    DATE(finished_at AT TIME ZONE 'UTC') AS date,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (
                        ORDER BY EXTRACT(EPOCH FROM (finished_at - queued_at))
                    ) AS p50,
                    PERCENTILE_CONT(0.95) WITHIN GROUP (
                        ORDER BY EXTRACT(EPOCH FROM (finished_at - queued_at))
                    ) AS p95,
                    PERCENTILE_CONT(0.99) WITHIN GROUP (
                        ORDER BY EXTRACT(EPOCH FROM (finished_at - queued_at))
                    ) AS p99
                FROM parse_jobs
                WHERE status = 'success' AND finished_at IS NOT NULL AND queued_at >= :since
                GROUP BY DATE(finished_at AT TIME ZONE 'UTC')
                ORDER BY date
            """),
            {"since": current_since},
        ).fetchall()
        parse_latency = [
            {
                "date": str(r.date),
                "p50": round(float(r.p50), 1) if r.p50 is not None else None,
                "p95": round(float(r.p95), 1) if r.p95 is not None else None,
                "p99": round(float(r.p99), 1) if r.p99 is not None else None,
            }
            for r in latency_rows
        ]

        fail_rate_rows = db.execute(
            text("""
                SELECT
                    DATE(queued_at AT TIME ZONE 'UTC') AS date,
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE status = 'failed') AS failed
                FROM parse_jobs
                WHERE queued_at >= :since
                GROUP BY DATE(queued_at AT TIME ZONE 'UTC')
                ORDER BY date
            """),
            {"since": current_since},
        ).fetchall()
        failure_rate = [
            {
                "date": str(r.date),
                "total": int(r.total),
                "failed": int(r.failed),
                "rate": round(int(r.failed) / int(r.total), 4) if r.total else 0,
            }
            for r in fail_rate_rows
        ]

        lowest_rows = db.execute(
            text("""
                SELECT
                    d.id, d.filename, d.status, d.confidence_score,
                    d.created_at, d.account_holder, d.account_number, d.statement_period,
                    MAX(al.created_at) AS last_modified_at,
                    (ARRAY_AGG(al.actor ORDER BY al.created_at DESC))[1] AS last_modified_by
                FROM documents d
                LEFT JOIN audit_log al ON al.document_id = d.id
                WHERE d.status != 'rejected' AND d.confidence_score IS NOT NULL
                GROUP BY d.id
                ORDER BY d.confidence_score ASC
                LIMIT 20
            """),
        ).fetchall()

        lowest = []
        for r in lowest_rows:
            last_change = r.last_modified_at if r.last_modified_at else r.created_at
            if last_change.tzinfo is None:
                last_change = last_change.replace(tzinfo=timezone.utc)
            time_in_status_h = round((now - last_change).total_seconds() / 3600, 1)
            lowest.append({
                "id": r.id,
                "filename": r.filename,
                "status": r.status,
                "confidence_score": round(float(r.confidence_score), 4) if r.confidence_score else None,
                "created_at": r.created_at.isoformat(),
                "account_holder": r.account_holder,
                "account_number": r.account_number,
                "statement_period": r.statement_period,
                "priority": compute_priority(r.status, r.confidence_score),
                "time_in_status_hours": time_in_status_h,
                "last_modified_by": r.last_modified_by,
            })

        return {
            "kpis": kpis,
            "alerts": alerts,
            "volume_vs_confidence": volume_vs_confidence,
            "pass_rate_trend": pass_rate_trend,
            "failure_breakdown": failure_breakdown,
            "by_source": by_source,
            "queue_depth": queue_depth,
            "parse_latency": parse_latency,
            "failure_rate": failure_rate,
            "lowest": lowest,
        }
    finally:
        db.close()
