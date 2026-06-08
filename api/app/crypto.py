"""
AES-256-GCM column-level encryption for PII fields.

Stored format: ``enc:v1:<base64(12-byte nonce || ciphertext || 16-byte tag)>``

The ``enc:v1:`` prefix allows the TypeDecorator to distinguish encrypted values
from legacy plaintext rows written before encryption was enabled, so a migration
can re-encrypt them without downtime.
"""
import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from .config import settings

_PREFIX = "enc:v1:"


def _get_key() -> bytes:
    """Load and validate the AES key from environment-backed settings."""

    raw = settings.column_encryption_key
    if not raw:
        raise RuntimeError(
            "COLUMN_ENCRYPTION_KEY is not set. "
            "Provide a 64-character hex string (32 bytes) in the environment."
        )
    try:
        key = bytes.fromhex(raw)
    except ValueError:
        raise RuntimeError("COLUMN_ENCRYPTION_KEY must be a 64-character hex string.")
    if len(key) != 32:
        raise RuntimeError("COLUMN_ENCRYPTION_KEY must be exactly 32 bytes (64 hex chars).")
    return key


def encrypt_value(plaintext: str) -> str:
    """Encrypt one plaintext string with a fresh nonce."""

    key = _get_key()
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return _PREFIX + base64.b64encode(nonce + ciphertext).decode()


def decrypt_value(stored: str) -> str:
    """Decrypt encrypted values while tolerating legacy plaintext rows."""

    if not stored.startswith(_PREFIX):
        return stored  # legacy plaintext — return as-is, migration will re-encrypt
    key = _get_key()
    data = base64.b64decode(stored[len(_PREFIX):])
    nonce, ciphertext = data[:12], data[12:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None).decode()


class EncryptedString(TypeDecorator):
    """Transparently encrypts/decrypts string columns with AES-256-GCM."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Encrypt values just before SQLAlchemy writes them to the database."""

        if value is None:
            return None
        return encrypt_value(value)

    def process_result_value(self, value, dialect):
        """Decrypt values when SQLAlchemy materializes ORM rows."""

        if value is None:
            return None
        return decrypt_value(value)
