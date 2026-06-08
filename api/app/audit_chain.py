"""Hash-chained audit log helpers.

Every audit entry stores sha256(prev_hash || canonical_row_data).
The first-ever row uses GENESIS_HASH ("0" * 64) as prev_hash.

Canonical row data:
    "{id}|{document_id}|{action}|{actor}|{created_at_isoformat}|{details_json}"

An advisory lock serialises concurrent inserts so the chain never branches.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from .models import AuditLog

logger = logging.getLogger(__name__)

GENESIS_HASH = "0" * 64
_LOCK_KEY = 42_424_242  # transaction-scoped advisory lock key


def _canonical(
    row_id: int,
    action: str,
    actor: str,
    created_at: datetime,
    details: dict,
) -> str:
    # document_id is intentionally excluded: it is a relational FK that can be
    # legitimately SET NULL (ON DELETE SET NULL) without constituting tampering.
    # Audit provenance is carried in the details JSONB payload.
    return "|".join([
        str(row_id),
        action,
        actor,
        created_at.isoformat(),
        json.dumps(details, sort_keys=True, separators=(",", ":")),
    ])


def compute_hash(
    prev_hash: str,
    row_id: int,
    action: str,
    actor: str,
    created_at: datetime,
    details: dict,
) -> str:
    payload = prev_hash + _canonical(row_id, action, actor, created_at, details)
    return hashlib.sha256(payload.encode()).hexdigest()


def add_audit_entry(
    db,
    doc_id: int | None,
    action: str,
    details: dict,
    actor: str,
) -> AuditLog:
    """Insert a hash-chained audit entry. Thread-safe via advisory lock."""
    # Serialise concurrent inserts within the same Postgres session
    db.execute(text(f"SELECT pg_advisory_xact_lock({_LOCK_KEY})"))

    prev = db.execute(
        text("SELECT hash_chain FROM audit_log ORDER BY id DESC LIMIT 1")
    ).fetchone()
    prev_hash = (prev[0] if prev and prev[0] else None) or GENESIS_HASH

    # Pre-allocate the PK so it's baked into the hash before the INSERT
    next_id = db.execute(text("SELECT nextval('audit_log_id_seq')")).scalar()
    now = datetime.now(timezone.utc)

    hash_val = compute_hash(prev_hash, next_id, action, actor, now, details)

    row = AuditLog(
        id=next_id,
        document_id=doc_id,
        action=action,
        actor=actor,
        details=details,
        created_at=now,
        hash_chain=hash_val,
    )
    db.add(row)
    return row


def verify_chain(db, since: datetime | None = None) -> dict:
    """Walk the hash chain and report the first break.

    Returns:
        chain_intact: bool
        rows_checked: int
        first_break_id: int | None
    """
    if since:
        rows = db.execute(
            text(
                "SELECT id, document_id, action, actor, created_at, details, hash_chain "
                "FROM audit_log WHERE created_at >= :since ORDER BY id"
            ),
            {"since": since},
        ).fetchall()
        # Expected prev_hash is the hash of the last row *before* the window
        prev_row = db.execute(
            text(
                "SELECT hash_chain FROM audit_log WHERE created_at < :since ORDER BY id DESC LIMIT 1"
            ),
            {"since": since},
        ).fetchone()
        expected_prev = (prev_row[0] if prev_row and prev_row[0] else None) or GENESIS_HASH
    else:
        rows = db.execute(
            text(
                "SELECT id, document_id, action, actor, created_at, details, hash_chain "
                "FROM audit_log ORDER BY id"
            )
        ).fetchall()
        expected_prev = GENESIS_HASH

    first_break_id = None
    for row in rows:
        row_id, document_id, action, actor, created_at, details, stored_hash = row
        if stored_hash is None:
            # Row pre-dates the hash chain feature — skip
            expected_prev = GENESIS_HASH
            continue
        expected = compute_hash(expected_prev, row_id, action, actor, created_at, details)
        if expected != stored_hash:
            first_break_id = row_id
            break
        expected_prev = stored_hash

    return {
        "chain_intact": first_break_id is None,
        "rows_checked": len(rows),
        "first_break_id": first_break_id,
    }
