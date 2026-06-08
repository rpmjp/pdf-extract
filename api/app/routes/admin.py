"""Administrative routes for security, audit, retention, and learning review."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from ..audit_chain import add_audit_entry, verify_chain
from ..auth import AuthUser, require_roles
from ..db import SessionLocal
from ..login_helpers import clear_login_failures
from ..models import AuditVerification, CorrectionExample, Document, RetentionPolicy, User
from ..serializers import parse_since, week_key

router = APIRouter(tags=["Admin"])


@router.post("/admin/users/{user_id}/unlock")
def unlock_user(user_id: int, user: Annotated[AuthUser, Depends(require_roles("admin"))]):
    """Clear Redis-backed login lockout state for a user."""

    db = SessionLocal()
    try:
        target = db.get(User, user_id)
        if not target:
            raise HTTPException(404, "User not found")
        clear_login_failures(target.username)
        return {"unlocked": True, "user_id": target.id, "username": target.username}
    finally:
        db.close()


@router.get("/admin/failures")
def admin_failures(
    user: Annotated[AuthUser, Depends(require_roles("admin"))],
    since: str = "30d",
):
    """Group correction examples into failure categories for prompt/rule work."""

    start = parse_since(since)
    db = SessionLocal()
    try:
        examples = (
            db.query(CorrectionExample)
            .filter(CorrectionExample.created_at >= start)
            .order_by(CorrectionExample.created_at.desc(), CorrectionExample.id.desc())
            .all()
        )
        grouped: dict[str, list[CorrectionExample]] = defaultdict(list)
        trends: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for example in examples:
            grouped[example.failure_category].append(example)
            trends[example.failure_category][week_key(example.created_at)] += 1

        categories = []
        for category, rows in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
            samples = []
            for example in rows[:3]:
                doc = db.get(Document, example.document_id)
                samples.append(
                    {
                        "document_id": example.document_id,
                        "filename": doc.filename if doc else "unknown",
                        "diffs": (example.field_diffs or [])[:2],
                    }
                )
            categories.append(
                {
                    "category": category,
                    "count": len(rows),
                    "samples": samples,
                    "trend": [{"week": week, "count": count} for week, count in sorted(trends[category].items())],
                }
            )
        return {"since": since, "categories": categories}
    finally:
        db.close()


@router.get("/admin/audit/verify")
def verify_audit_chain(
    user: Annotated[AuthUser, Depends(require_roles("admin"))],
    since: str | None = None,
):
    """Walk the hash chain and report any tampered rows.

    Query params:
      since — ISO date (YYYY-MM-DD) or relative (30d/8w). Omit to verify from genesis.
    """
    since_dt: datetime | None = None
    if since:
        since_dt = parse_since(since)

    db = SessionLocal()
    try:
        result = verify_chain(db, since=since_dt)

        # Persist the verification record
        verification = AuditVerification(
            verified_at=datetime.now(timezone.utc),
            since=since_dt,
            rows_checked=result["rows_checked"],
            chain_intact=result["chain_intact"],
            first_break_id=result["first_break_id"],
        )
        db.add(verification)
        db.commit()

        return {
            "chain_intact": result["chain_intact"],
            "rows_checked": result["rows_checked"],
            "first_break_id": result["first_break_id"],
            "since": since_dt.isoformat() if since_dt else None,
            "verification_id": verification.id,
        }
    finally:
        db.close()


@router.post("/admin/retention/dry-run")
def retention_dry_run(
    user: Annotated[AuthUser, Depends(require_roles("admin"))],
):
    """Show which documents would be deleted today without actually deleting them.

    Must be reviewed before any retention policy change takes effect.
    """
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        policies = (
            db.query(RetentionPolicy)
            .filter(RetentionPolicy.is_active == True)  # noqa: E712
            .all()
        )

        would_delete: list[dict] = []
        for policy in policies:
            statuses = [s.strip() for s in policy.status_pattern.split(",") if s.strip()]
            cutoff = now - timedelta(days=policy.retention_days)

            expired = (
                db.query(Document)
                .filter(
                    Document.status.in_(statuses),
                    Document.created_at < cutoff,
                    Document.deleted_at.is_(None),
                )
                .all()
            )
            for doc in expired:
                would_delete.append(
                    {
                        "document_id": doc.id,
                        "filename": doc.filename,
                        "status": doc.status,
                        "created_at": doc.created_at.isoformat(),
                        "age_days": (now - doc.created_at).days,
                        "policy_id": policy.id,
                        "policy_status_pattern": policy.status_pattern,
                        "retention_days": policy.retention_days,
                    }
                )

        return {
            "dry_run": True,
            "evaluated_at": now.isoformat(),
            "would_delete_count": len(would_delete),
            "would_delete": would_delete,
        }
    finally:
        db.close()


@router.get("/admin/retention/policies")
def list_retention_policies(
    user: Annotated[AuthUser, Depends(require_roles("admin"))],
):
    """List configured retention policies without applying any deletion."""

    db = SessionLocal()
    try:
        policies = db.query(RetentionPolicy).order_by(RetentionPolicy.id).all()
        return [
            {
                "id": p.id,
                "applies_to": p.applies_to,
                "status_pattern": p.status_pattern,
                "retention_days": p.retention_days,
                "deletion_strategy": p.deletion_strategy,
                "is_active": p.is_active,
                "created_at": p.created_at.isoformat(),
            }
            for p in policies
        ]
    finally:
        db.close()
