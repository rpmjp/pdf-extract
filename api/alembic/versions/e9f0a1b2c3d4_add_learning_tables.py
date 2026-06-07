"""add learning tables

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-06-07 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e9f0a1b2c3d4"
down_revision: Union[str, None] = "d8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "correction_examples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("original_version_id", sa.Integer(), nullable=False),
        sa.Column("corrected_version_id", sa.Integer(), nullable=False),
        sa.Column("field_diffs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("failure_category", sa.String(length=64), nullable=False),
        sa.Column("pdf_features", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["corrected_version_id"], ["document_versions.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["original_version_id"], ["document_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_correction_examples_document_id", "correction_examples", ["document_id"])
    op.create_index("ix_correction_examples_failure_category", "correction_examples", ["failure_category"])

    op.create_table(
        "eval_set_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("eval_set_version", sa.String(length=32), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("corrected_version_id", sa.Integer(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["corrected_version_id"], ["document_versions.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("eval_set_version", "document_id", name="uq_eval_set_member_version_document"),
    )
    op.create_index("ix_eval_set_members_version", "eval_set_members", ["eval_set_version"])

    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("eval_set_version", sa.String(length=32), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("ran_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("eval_runs")
    op.drop_index("ix_eval_set_members_version", table_name="eval_set_members")
    op.drop_table("eval_set_members")
    op.drop_index("ix_correction_examples_failure_category", table_name="correction_examples")
    op.drop_index("ix_correction_examples_document_id", table_name="correction_examples")
    op.drop_table("correction_examples")
