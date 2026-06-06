"""add document extraction fields

Revision ID: f3d8c1a2b4e9
Revises: 884a4a5db3e7
Create Date: 2026-06-06 17:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f3d8c1a2b4e9"
down_revision: Union[str, None] = "884a4a5db3e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("account_holder", sa.String(length=255), nullable=True))
    op.add_column("documents", sa.Column("account_number", sa.String(length=64), nullable=True))
    op.add_column("documents", sa.Column("statement_period", sa.String(length=128), nullable=True))
    op.add_column("documents", sa.Column("opening_balance", sa.Numeric(precision=14, scale=2), nullable=True))
    op.add_column("documents", sa.Column("closing_balance", sa.Numeric(precision=14, scale=2), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "closing_balance")
    op.drop_column("documents", "opening_balance")
    op.drop_column("documents", "statement_period")
    op.drop_column("documents", "account_number")
    op.drop_column("documents", "account_holder")
