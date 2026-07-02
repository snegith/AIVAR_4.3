"""SQLAlchemy ORM models for detector_db.

Maps the five source-of-truth tables: sessions, interactions (with pgvector
embeddings), user_risk_profiles, detected_patterns, and alerts.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all detector_db ORM models."""


class SessionRow(Base):
    """A user session — unit for cross-session counting (20 / 50 sessions)."""

    __tablename__ = "sessions"
    __table_args__ = (Index("idx_sessions_user_started", "user_id", "started_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    interaction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    session_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)

    interactions: Mapped[list[InteractionRow]] = relationship(back_populates="session")


class InteractionRow(Base):
    """Event store row — source of truth for detection and embeddings."""

    __tablename__ = "interactions"
    __table_args__ = (
        CheckConstraint(
            "guardrail_outcome IN ('allowed', 'blocked', 'flagged')",
            name="ck_interactions_guardrail_outcome",
        ),
        Index("idx_inter_user_ts", "user_id", "ts"),
        Index("idx_inter_outcome", "user_id", "guardrail_outcome"),
        Index("idx_inter_template", "user_id", "template_signature"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    guardrail_outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    guardrail_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    capability_level: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    template_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    numeric_tokens: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    langfuse_trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    session: Mapped[SessionRow] = relationship(back_populates="interactions")


class UserRiskProfileRow(Base):
    """Per-user risk accumulator with optimistic-lock version column."""

    __tablename__ = "user_risk_profiles"
    __table_args__ = (
        CheckConstraint(
            "status IN ('normal', 'watch', 'alerted')",
            name="ck_user_risk_profiles_status",
        ),
    )

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    risk_score: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, default=Decimal("0")
    )
    signal_probing: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    signal_escalation: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    signal_enumeration: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    session_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    interaction_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DetectedPatternRow(Base):
    """Per-technique detection record with evidence for threat cards."""

    __tablename__ = "detected_patterns"
    __table_args__ = (
        CheckConstraint(
            "pattern_type IN ('probing', 'escalation', 'enumeration')",
            name="ck_detected_patterns_pattern_type",
        ),
        Index("idx_pattern_user_time", "user_id", "detected_at"),
        Index("idx_pattern_type", "pattern_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    pattern_type: Mapped[str] = mapped_column(String(32), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signal_strength: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    contributing_interaction_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True
    )


class AlertRow(Base):
    """Alert raised when composite risk crosses the configured threshold."""

    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'ack', 'resolved')",
            name="ck_alerts_status",
        ),
        Index("idx_alerts_user", "user_id", "created_at"),
        Index("idx_alerts_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    risk_score_at_alert: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    threshold: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    dominant_pattern: Mapped[str | None] = mapped_column(Text, nullable=True)
    pattern_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    contributing_pattern_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
