from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from visionpass.db import Base


class UserRole(StrEnum):
    ADMIN = "admin"
    REVIEWER = "reviewer"


class TemplateStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    REJECTED = "rejected"
    DELETED = "deleted"


class AccessDecision(StrEnum):
    GRANTED = "granted"
    DENIED = "denied"
    REVIEW = "review"


class UUIDMixin:
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'reviewer')", name="ck_users_role"),
        UniqueConstraint("email", name="users_email_key"),
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(String(20), nullable=False)


class Event(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "events"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Participant(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "participants"
    __table_args__ = (
        UniqueConstraint("event_id", "external_id", name="participants_event_external_key"),
        UniqueConstraint("event_id", "email", name="participants_event_email_key"),
        Index("ix_participants_event_created", "event_id", "created_at"),
    )

    event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(80), nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    template: Mapped["BiometricTemplate | None"] = relationship(
        back_populates="participant", uselist=False
    )


class BiometricTemplate(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "biometric_templates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'rejected', 'deleted')",
            name="ck_templates_status",
        ),
        UniqueConstraint("participant_id", name="biometric_templates_participant_key"),
    )

    participant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("participants.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[TemplateStatus] = mapped_column(String(20), nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(JSONB(none_as_null=True))
    pending_image: Mapped[bytes | None] = mapped_column(LargeBinary)
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    participant: Mapped[Participant] = relationship(back_populates="template")


class AccessAttempt(UUIDMixin, Base):
    __tablename__ = "access_attempts"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('granted', 'denied', 'review')",
            name="ck_access_attempts_decision",
        ),
        Index("ix_access_attempts_event_created", "event_id", "created_at"),
    )

    event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    participant_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("participants.id", ondelete="SET NULL")
    )
    decision: Mapped[AccessDecision] = mapped_column(String(20), nullable=False)
    distance: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ManualReview(UUIDMixin, Base):
    __tablename__ = "manual_reviews"
    __table_args__ = (UniqueConstraint("attempt_id", name="manual_reviews_attempt_key"),)

    attempt_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("access_attempts.id", ondelete="CASCADE"), nullable=False
    )
    reviewer_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    resolution: Mapped[AccessDecision] = mapped_column(String(20), nullable=False)
    note: Mapped[str] = mapped_column(String(1000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditLog(UUIDMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_created", "created_at"),)

    actor_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OutboxEvent(UUIDMixin, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_pending", "published_at", "created_at"),)

    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
