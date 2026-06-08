"""add security columns: scan_status, widen PII cols, pgcrypto ext, re-encrypt rows

Revision ID: b7c8d9e0f1a2
Revises: ab12cd34ef56
Create Date: 2026-06-07 00:00:00.000000

Migration steps
---------------
1. CREATE EXTENSION pgcrypto (idempotent — used for reference / future DB-side ops).
2. Widen account_holder (VARCHAR→TEXT) and account_number (VARCHAR→TEXT) so the
   AES-GCM ciphertext (base64 prefix + nonce + tag overhead) always fits.
3. Add scan_status VARCHAR(16) DEFAULT 'unscanned'.
4. Re-encrypt existing plaintext rows using the COLUMN_ENCRYPTION_KEY from env.
   If the env var is absent the step is skipped and rows remain plaintext; the
   TypeDecorator will still return them correctly via its legacy-fallback path.
"""
import base64
import logging
import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "ab12cd34ef56"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger(__name__)


def _encrypt_value(plaintext: str, key: bytes) -> str:
    """AES-256-GCM encrypt matching the EncryptedString TypeDecorator format."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return "enc:v1:" + base64.b64encode(nonce + ciphertext).decode()


def upgrade() -> None:
    # 1. pgcrypto extension (idempotent)
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # 2. Widen PII columns so encrypted values fit
    op.alter_column("documents", "account_holder", type_=sa.Text(), existing_nullable=True)
    op.alter_column("documents", "account_number", type_=sa.Text(), existing_nullable=True)

    # 3. Add scan_status column
    op.add_column(
        "documents",
        sa.Column("scan_status", sa.String(16), server_default="unscanned", nullable=False),
    )

    # 4. Re-encrypt existing plaintext PII rows
    key_hex = os.environ.get("COLUMN_ENCRYPTION_KEY", "").strip()
    if not key_hex or len(key_hex) != 64:
        logger.warning(
            "COLUMN_ENCRYPTION_KEY not set or invalid — existing PII rows left as plaintext. "
            "Run tools/encrypt_pii.py after setting the key."
        )
        return

    try:
        key = bytes.fromhex(key_hex)
    except ValueError:
        logger.warning("COLUMN_ENCRYPTION_KEY is not valid hex — skipping PII re-encryption.")
        return

    conn = op.get_bind()
    rows = conn.execute(
        text(
            "SELECT id, account_holder, account_number FROM documents "
            "WHERE account_holder IS NOT NULL OR account_number IS NOT NULL"
        )
    ).fetchall()

    for row in rows:
        ah = row.account_holder
        an = row.account_number
        if ah and not ah.startswith("enc:v1:"):
            ah = _encrypt_value(ah, key)
        if an and not an.startswith("enc:v1:"):
            an = _encrypt_value(an, key)
        if ah != row.account_holder or an != row.account_number:
            conn.execute(
                text("UPDATE documents SET account_holder=:ah, account_number=:an WHERE id=:id"),
                {"ah": ah, "an": an, "id": row.id},
            )

    logger.info("Re-encrypted PII fields for %d document rows.", len(rows))


def downgrade() -> None:
    op.drop_column("documents", "scan_status")
    op.alter_column("documents", "account_number", type_=sa.String(64), existing_nullable=True)
    op.alter_column("documents", "account_holder", type_=sa.String(255), existing_nullable=True)
    # Note: pgcrypto extension and any re-encrypted data are NOT reversed on downgrade.
