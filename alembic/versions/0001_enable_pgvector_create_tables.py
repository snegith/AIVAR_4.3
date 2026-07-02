"""Enable pgvector and create all detector_db tables.

Revision ID: 0001
Revises:
Create Date: 2026-07-02

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
  op.execute("CREATE EXTENSION IF NOT EXISTS vector")

  op.create_table(
      "sessions",
      sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
      sa.Column("user_id", sa.Text(), nullable=False),
      sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
      sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=False),
      sa.Column("interaction_count", sa.Integer(), nullable=False, server_default="0"),
      sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
  )
  op.create_index(
      "idx_sessions_user_started",
      "sessions",
      ["user_id", sa.text("started_at DESC")],
  )

  op.create_table(
      "interactions",
      sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
      sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sessions.id"), nullable=False),
      sa.Column("user_id", sa.Text(), nullable=False),
      sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
      sa.Column("prompt", sa.Text(), nullable=False),
      sa.Column("normalized_prompt", sa.Text(), nullable=True),
      sa.Column("response", sa.Text(), nullable=True),
      sa.Column("guardrail_outcome", sa.String(16), nullable=False),
      sa.Column("guardrail_reason", sa.Text(), nullable=True),
      sa.Column("capability_level", sa.SmallInteger(), nullable=True),
      sa.Column("embedding", Vector(384), nullable=True),
      sa.Column("template_signature", sa.Text(), nullable=True),
      sa.Column("numeric_tokens", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
      sa.Column("langfuse_trace_id", sa.Text(), nullable=True),
      sa.Column("latency_ms", sa.Integer(), nullable=True),
      sa.Column("model", sa.Text(), nullable=True),
      sa.Column("is_degraded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
      sa.CheckConstraint(
          "guardrail_outcome IN ('allowed', 'blocked', 'flagged')",
          name="ck_interactions_guardrail_outcome",
      ),
  )
  op.create_index("idx_inter_user_ts", "interactions", ["user_id", sa.text("ts DESC")])
  op.create_index("idx_inter_outcome", "interactions", ["user_id", "guardrail_outcome"])
  op.create_index("idx_inter_template", "interactions", ["user_id", "template_signature"])
  op.execute(
      """
      CREATE INDEX idx_inter_embedding ON interactions
      USING hnsw (embedding vector_cosine_ops)
      WITH (m = 16, ef_construction = 64)
      """
  )

  op.create_table(
      "user_risk_profiles",
      sa.Column("user_id", sa.Text(), primary_key=True),
      sa.Column("risk_score", sa.Numeric(6, 2), nullable=False, server_default="0"),
      sa.Column("signal_probing", sa.Numeric(5, 4), nullable=True),
      sa.Column("signal_escalation", sa.Numeric(5, 4), nullable=True),
      sa.Column("signal_enumeration", sa.Numeric(5, 4), nullable=True),
      sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
      sa.Column("last_scored_at", sa.DateTime(timezone=True), nullable=True),
      sa.Column("session_count", sa.Integer(), nullable=True),
      sa.Column("interaction_count", sa.Integer(), nullable=True),
      sa.Column("status", sa.String(16), nullable=False, server_default="normal"),
      sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
      sa.CheckConstraint(
          "status IN ('normal', 'watch', 'alerted')",
          name="ck_user_risk_profiles_status",
      ),
  )

  op.create_table(
      "detected_patterns",
      sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
      sa.Column("user_id", sa.Text(), nullable=False),
      sa.Column("pattern_type", sa.String(32), nullable=False),
      sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
      sa.Column("signal_strength", sa.Numeric(5, 4), nullable=True),
      sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
      sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
      sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
      sa.Column(
          "contributing_interaction_ids",
          postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
          nullable=True,
      ),
      sa.CheckConstraint(
          "pattern_type IN ('probing', 'escalation', 'enumeration')",
          name="ck_detected_patterns_pattern_type",
      ),
  )
  op.create_index(
      "idx_pattern_user_time",
      "detected_patterns",
      ["user_id", sa.text("detected_at DESC")],
  )
  op.create_index("idx_pattern_type", "detected_patterns", ["pattern_type"])

  op.create_table(
      "alerts",
      sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
      sa.Column("user_id", sa.Text(), nullable=False),
      sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
      sa.Column("risk_score_at_alert", sa.Numeric(6, 2), nullable=True),
      sa.Column("threshold", sa.Numeric(6, 2), nullable=True),
      sa.Column("dominant_pattern", sa.Text(), nullable=True),
      sa.Column("pattern_breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
      sa.Column("summary", sa.Text(), nullable=True),
      sa.Column(
          "contributing_pattern_ids",
          postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
          nullable=True,
      ),
      sa.Column("status", sa.String(16), nullable=False, server_default="open"),
      sa.CheckConstraint(
          "status IN ('open', 'ack', 'resolved')",
          name="ck_alerts_status",
      ),
  )
  op.create_index("idx_alerts_user", "alerts", ["user_id", sa.text("created_at DESC")])
  op.create_index("idx_alerts_status", "alerts", ["status"])


def downgrade() -> None:
  op.drop_index("idx_alerts_status", table_name="alerts")
  op.drop_index("idx_alerts_user", table_name="alerts")
  op.drop_table("alerts")
  op.drop_index("idx_pattern_type", table_name="detected_patterns")
  op.drop_index("idx_pattern_user_time", table_name="detected_patterns")
  op.drop_table("detected_patterns")
  op.drop_table("user_risk_profiles")
  op.execute("DROP INDEX IF EXISTS idx_inter_embedding")
  op.drop_index("idx_inter_template", table_name="interactions")
  op.drop_index("idx_inter_outcome", table_name="interactions")
  op.drop_index("idx_inter_user_ts", table_name="interactions")
  op.drop_table("interactions")
  op.drop_index("idx_sessions_user_started", table_name="sessions")
  op.drop_table("sessions")
  op.execute("DROP EXTENSION IF EXISTS vector")
