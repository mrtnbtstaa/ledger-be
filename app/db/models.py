import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, LargeBinary, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Receipt(Base):
    __tablename__ = "receipts"
    __table_args__ = (
        Index(
            "uq_receipts_file_sha256_upload",
            "file_sha256",
            unique=True,
            postgresql_where=text("sample_id IS NULL"),
        ),
        Index("ix_receipts_status_uploaded_at", "status", "uploaded_at"),
        Index("ix_receipts_uploaded_at", "uploaded_at"),
        Index("ix_receipts_data_category", text("((data ->> 'category'))")),
        Index(
            "ix_receipts_data_total",
            text("((data ->> 'total')::numeric) DESC NULLS LAST"),
        ),
        Index("ix_receipts_sample_id", "sample_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(8))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(32), default="processing")
    stage: Mapped[str] = mapped_column(String(32), default="uploading")
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited: Mapped[bool] = mapped_column(default=False)
    cached: Mapped[bool] = mapped_column(default=False)
    sample_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pinned: Mapped[bool] = mapped_column(default=False)
    original_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ReceiptFile(Base):
    __tablename__ = "receipt_files"

    receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("receipts.id", ondelete="CASCADE"), primary_key=True
    )
    content: Mapped[bytes] = mapped_column(LargeBinary)
    content_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SheetSync(Base):
    __tablename__ = "sheet_sync"
    __table_args__ = (Index("ix_sheet_sync_state_updated_at", "state", "updated_at"),)

    receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("receipts.id", ondelete="CASCADE"), primary_key=True
    )
    state: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_receipt_at", "receipt_id", "at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    receipt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("receipts.id", ondelete="CASCADE"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    type: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(16), default="system")
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class LLMCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("receipts.id", ondelete="CASCADE"), nullable=True
    )
    purpose: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(96))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer)
    fallback_used: Mapped[bool] = mapped_column(default=False)
    outcome: Mapped[str] = mapped_column(String(32))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LLMBudget(Base):
    __tablename__ = "llm_budget"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)
