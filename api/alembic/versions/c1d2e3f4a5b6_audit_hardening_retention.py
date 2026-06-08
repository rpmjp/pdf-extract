"""audit ledger hardening + data retention

Revision ID: c1d2e3f4a5b6
Revises: b7c8d9e0f1a2
Create Date: 2026-06-07 00:00:00.000000

Migration steps
---------------
1. Widen audit_log.action from VARCHAR(32) to VARCHAR(64).
2. Make audit_log.document_id nullable with ON DELETE SET NULL.
3. Add audit_log.hash_chain VARCHAR(64) (nullable; backfilled below).
4. Create retention_policies table and seed default policies.
5. Add documents.deleted_at and documents.deletion_policy_id.
6. Create audit_verifications table.
7. Create the audit_writer Postgres role (INSERT-only on audit_log).
8. Backfill hash_chain for existing audit_log rows (chronological).
"""
import hashlib
import json
import logging
import os
from datetime import timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger(__name__)

GENESIS_HASH = "0" * 64


def _canonical(row_id, action, actor, created_at, details) -> str:
    # document_id excluded: it is a relational FK that can be SET NULL without tampering.
    return "|".join([
        str(row_id),
        action,
        actor,
        created_at.isoformat(),
        json.dumps(details, sort_keys=True, separators=(",", ":")),
    ])


def _compute_hash(prev_hash, row_id, action, actor, created_at, details) -> str:
    payload = prev_hash + _canonical(row_id, action, actor, created_at, details)
    return hashlib.sha256(payload.encode()).hexdigest()


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Widen action column
    op.alter_column("audit_log", "action", type_=sa.String(64), existing_nullable=False)

    # 2. Make document_id nullable with ON DELETE SET NULL
    op.alter_column("audit_log", "document_id", existing_type=sa.Integer(), nullable=True)
    # Drop existing FK, recreate with ON DELETE SET NULL
    op.drop_constraint("audit_log_document_id_fkey", "audit_log", type_="foreignkey")
    op.create_foreign_key(
        "audit_log_document_id_fkey",
        "audit_log", "documents",
        ["document_id"], ["id"],
        ondelete="SET NULL",
    )

    # 3. Add hash_chain column
    op.add_column("audit_log", sa.Column("hash_chain", sa.String(64), nullable=True))

    # 4. retention_policies table
    op.create_table(
        "retention_policies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("applies_to", sa.String(32), nullable=False),
        sa.Column("status_pattern", sa.String(128), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("deletion_strategy", sa.String(32), nullable=False, server_default="hard_delete"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    conn.execute(
        text(
            "INSERT INTO retention_policies (applies_to, status_pattern, retention_days, deletion_strategy)"
            " VALUES"
            " ('documents', 'approved', 2555, 'hard_delete'),"     # 7 years
            " ('documents', 'failed,rejected', 90, 'hard_delete')"
        )
    )

    # 5. Document tombstone columns
    op.add_column("documents", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("deletion_policy_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "documents_deletion_policy_id_fkey",
        "documents", "retention_policies",
        ["deletion_policy_id"], ["id"],
    )

    # 6. audit_verifications table
    op.create_table(
        "audit_verifications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rows_checked", sa.Integer(), nullable=False),
        sa.Column("chain_intact", sa.Boolean(), nullable=False),
        sa.Column("first_break_id", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    # 7. Create audit_writer role
    # DO blocks don't support bound parameters; embed the password with single-quote escaping.
    audit_writer_password = os.environ.get("AUDIT_WRITER_PASSWORD", "audit_writer_dev_changeme")
    safe_pw = audit_writer_password.replace("'", "''")  # SQL-escape single quotes
    conn.execute(text(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'audit_writer') THEN
                CREATE ROLE audit_writer WITH LOGIN PASSWORD '{safe_pw}' NOINHERIT;
            END IF;
        END
        $$
    """))

    # Grant SELECT + INSERT; explicitly revoke UPDATE and DELETE
    conn.execute(text("GRANT SELECT, INSERT ON audit_log TO audit_writer"))
    conn.execute(text("GRANT SELECT ON audit_verifications TO audit_writer"))
    conn.execute(text("GRANT USAGE ON SEQUENCE audit_log_id_seq TO audit_writer"))
    conn.execute(text("REVOKE UPDATE, DELETE ON audit_log FROM audit_writer"))

    logger.info("audit_writer role configured with SELECT + INSERT on audit_log.")

    # 8. Backfill hash_chain for existing audit_log rows
    rows = conn.execute(
        text(
            "SELECT id, document_id, action, actor, created_at, details "
            "FROM audit_log ORDER BY id"
        )
    ).fetchall()

    prev_hash = GENESIS_HASH
    for row in rows:
        row_id, document_id, action, actor, created_at, details = row
        # Ensure created_at is timezone-aware for isoformat
        if created_at.tzinfo is None:
            from datetime import datetime
            created_at = created_at.replace(tzinfo=timezone.utc)
        h = _compute_hash(prev_hash, row_id, action, actor, created_at, details)
        conn.execute(
            text("UPDATE audit_log SET hash_chain = :h WHERE id = :id"),
            {"h": h, "id": row_id},
        )
        prev_hash = h

    logger.info("Backfilled hash_chain for %d existing audit_log rows.", len(rows))


def downgrade() -> None:
    op.drop_constraint("documents_deletion_policy_id_fkey", "documents", type_="foreignkey")
    op.drop_column("documents", "deletion_policy_id")
    op.drop_column("documents", "deleted_at")
    op.drop_table("audit_verifications")
    op.drop_table("retention_policies")
    op.drop_column("audit_log", "hash_chain")
    op.drop_constraint("audit_log_document_id_fkey", "audit_log", type_="foreignkey")
    op.alter_column("audit_log", "document_id", existing_type=sa.Integer(), nullable=False)
    op.create_foreign_key(
        "audit_log_document_id_fkey",
        "audit_log", "documents",
        ["document_id"], ["id"],
    )
    op.alter_column("audit_log", "action", type_=sa.String(32), existing_nullable=False)
