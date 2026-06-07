"""add confidence scores

Revision ID: c7d8e9f0a1b2
Revises: b2c3d4e5f6a7
Create Date: 2026-06-07 02:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("confidence_score", sa.Numeric(5, 2), nullable=True))
    op.add_column("transactions", sa.Column("confidence", sa.Numeric(5, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "confidence")
    op.drop_column("documents", "confidence_score")
