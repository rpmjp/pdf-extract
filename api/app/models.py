"""SQLAlchemy ORM models for the extraction and review workflow.

The schema separates immutable source artifacts, derived extraction data,
operational job state, and governance records such as audit logs, versions, and
correction examples.
"""
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .crypto import EncryptedString


class Base(DeclarativeBase):
    pass


class User(Base):
    """Application user with roles used by route authorization."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    roles: Mapped[list[str]] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class RefreshToken(Base):
    """Hashed, rotating refresh token record for browser sessions."""
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_from_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Document(Base):
    """Uploaded PDF plus the current extraction/review state."""
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    minio_key: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="uploaded")
    scan_status: Mapped[str] = mapped_column(String(16), default="unscanned", server_default="unscanned")
    current_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_holder: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    account_number: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    statement_period: Mapped[str | None] = mapped_column(String(128), nullable=True)
    opening_balance: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    closing_balance: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deletion_policy_id: Mapped[int | None] = mapped_column(ForeignKey("retention_policies.id"), nullable=True)

class Transaction(Base):
    """One parsed bank-statement transaction row."""
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    txn_date: Mapped[str] = mapped_column(String(10))
    description: Mapped[str] = mapped_column(Text)
    amount: Mapped[float] = mapped_column(Numeric(14, 2))
    type: Mapped[str] = mapped_column(String(12))
    balance: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)


class ReviewItem(Base):
    """Actionable reason a document requires human inspection."""
    __tablename__ = "review_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class AuditLog(Base):
    """Append-only user/system action record with tamper-evident hashes."""
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSONB)
    actor: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    hash_chain: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ParseJob(Base):
    """Durable record of an async Celery parse task."""
    __tablename__ = "parse_jobs"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    status: Mapped[str] = mapped_column(String(32))
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(default=0)
    worker_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class DocumentVersion(Base):
    """Snapshot of extracted state after parse or reviewer edits."""
    __tablename__ = "document_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    source: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(80))
    data: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class CorrectionExample(Base):
    """Learning example produced from reviewer-approved corrections."""
    __tablename__ = "correction_examples"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    original_version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"))
    corrected_version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"))
    field_diffs: Mapped[list[dict]] = mapped_column(JSONB)
    failure_category: Mapped[str] = mapped_column(String(64))
    pdf_features: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class EvalSetMember(Base):
    __tablename__ = "eval_set_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    eval_set_version: Mapped[str] = mapped_column(String(32))
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    corrected_version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"))
    locked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    eval_set_version: Mapped[str] = mapped_column(String(32))
    prompt_version: Mapped[str] = mapped_column(String(32))
    ran_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    metrics_json: Mapped[dict] = mapped_column(JSONB)


class RetentionPolicy(Base):
    __tablename__ = "retention_policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    applies_to: Mapped[str] = mapped_column(String(32))         # e.g. "documents"
    status_pattern: Mapped[str] = mapped_column(String(128))    # comma-separated statuses
    retention_days: Mapped[int] = mapped_column()
    deletion_strategy: Mapped[str] = mapped_column(String(32), default="hard_delete")
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class AuditVerification(Base):
    __tablename__ = "audit_verifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rows_checked: Mapped[int] = mapped_column()
    chain_intact: Mapped[bool] = mapped_column()
    first_break_id: Mapped[int | None] = mapped_column(nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
