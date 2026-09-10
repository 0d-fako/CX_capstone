"""initial: the eleven MVP tables (doc 09 §4)

Revision ID: 0001
Revises:
Create Date: 2026-09-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _uuid_pk() -> sa.Column:
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True)


def _now() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    # ---- tenancy
    op.create_table(
        "tenants",
        _uuid_pk(),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("industry", sa.String(120), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="prospect"),
        sa.Column("venture_brief", sa.Text),
        sa.Column("geography", sa.String(120)),
        sa.Column("slack_channel", sa.String(120)),
        sa.Column("created_from_session_id", pg.UUID(as_uuid=True)),
        _now(),
    )

    op.create_table(
        "targets",
        _uuid_pk(),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("platform", sa.String(24), nullable=False),
        sa.Column("handle", sa.String(120), nullable=False),
        sa.Column("ownership", sa.String(16), nullable=False, server_default="competitor"),
        sa.Column("role", sa.String(16), nullable=False, server_default="direct"),
        sa.Column("source", sa.String(32), nullable=False, server_default="scrapecreators"),
        sa.Column("follower_count", sa.Integer),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        _now(),
        sa.UniqueConstraint("tenant_id", "platform", "handle", name="uq_target"),
    )
    op.create_index("ix_targets_tenant_id", "targets", ["tenant_id"])

    op.create_table(
        "chat_sessions",
        _uuid_pk(),
        sa.Column("user", sa.String(120), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False, server_default="cli"),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenants.id")),
        sa.Column("stage", sa.String(16), nullable=False, server_default="interview"),
        sa.Column("messages", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("credits_used", sa.Integer, nullable=False, server_default="0"),
        _now(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_chat_sessions_tenant_id", "chat_sessions", ["tenant_id"])

    # ---- data layer
    op.create_table(
        "raw_captures",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("target_id", pg.UUID(as_uuid=True), sa.ForeignKey("targets.id"), nullable=False),
        sa.Column(
            "captured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("payload", pg.JSONB, nullable=False),
    )
    op.create_index("ix_raw_captures_tenant_id", "raw_captures", ["tenant_id"])
    op.create_index("ix_raw_captures_target_id", "raw_captures", ["target_id"])

    op.create_table(
        "posts",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("target_id", pg.UUID(as_uuid=True), sa.ForeignKey("targets.id"), nullable=False),
        sa.Column("platform", sa.String(24), nullable=False),
        sa.Column("post_id", sa.String(160), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content", sa.Text),
        sa.Column("media_type", sa.String(24)),
        sa.Column("media_duration_s", sa.Integer),
        sa.Column("url", sa.Text),
        sa.Column(
            "first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "platform", "post_id", name="uq_post_tenant_platform_id"),
    )
    op.create_index("ix_posts_tenant_id", "posts", ["tenant_id"])
    op.create_index("ix_posts_target_id", "posts", ["target_id"])
    op.create_index("ix_posts_tenant_posted", "posts", ["tenant_id", "posted_at"])

    op.create_table(
        "metric_snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("post_id", sa.BigInteger, sa.ForeignKey("posts.id"), nullable=False),
        sa.Column("captured_on", sa.Date, nullable=False),
        sa.Column("likes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("comments", sa.Integer, nullable=False, server_default="0"),
        sa.Column("shares", sa.Integer, nullable=False, server_default="0"),
        sa.Column("views", sa.Integer),
        sa.UniqueConstraint("post_id", "captured_on", name="uq_snapshot_post_day"),
    )
    op.create_index("ix_metric_snapshots_post_id", "metric_snapshots", ["post_id"])

    op.create_table(
        "post_labels",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("post_id", sa.BigInteger, sa.ForeignKey("posts.id"), nullable=False),
        sa.Column("dimension", sa.String(64), nullable=False),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("model_id", sa.String(64)),
        sa.Column("definition_hash", sa.String(64), nullable=False),
        sa.Column("run_id", pg.UUID(as_uuid=True)),
        _now(),
        sa.UniqueConstraint("post_id", "dimension", "definition_hash", name="uq_label_post_dim_def"),
    )
    op.create_index("ix_post_labels_post_id", "post_labels", ["post_id"])
    op.create_index("ix_post_labels_dim", "post_labels", ["dimension", "definition_hash"])

    # ---- agent & review
    op.create_table(
        "agent_runs",
        _uuid_pk(),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenants.id")),
        sa.Column("session_id", pg.UUID(as_uuid=True), sa.ForeignKey("chat_sessions.id")),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("model_id", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("thresholds_version", sa.String(16), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("tool_calls", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("draft", sa.Text),
        sa.Column("validation", pg.JSONB),
        sa.Column("usage", pg.JSONB),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
    )
    op.create_index("ix_agent_runs_tenant_id", "agent_runs", ["tenant_id"])
    op.create_index("ix_agent_runs_session_id", "agent_runs", ["session_id"])

    op.create_table(
        "reports",
        _uuid_pk(),
        sa.Column("run_id", pg.UUID(as_uuid=True), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("period", sa.String(32), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        _now(),
    )
    op.create_index("ix_reports_tenant_id", "reports", ["tenant_id"])

    op.create_table(
        "review_feedback",
        _uuid_pk(),
        sa.Column("report_id", pg.UUID(as_uuid=True), sa.ForeignKey("reports.id"), nullable=False),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("reviewer", sa.String(120), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("notes", sa.Text),
        _now(),
    )
    op.create_index("ix_review_feedback_report_id", "review_feedback", ["report_id"])
    op.create_index("ix_review_feedback_tenant_id", "review_feedback", ["tenant_id"])

    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenants.id")),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("items", sa.Integer, nullable=False, server_default="0"),
        sa.Column("credits_used", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("error", sa.Text),
        sa.Column("detail", pg.JSONB),
    )
    op.create_index("ix_pipeline_runs_tenant_id", "pipeline_runs", ["tenant_id"])


def downgrade() -> None:
    for t in (
        "pipeline_runs", "review_feedback", "reports", "agent_runs", "post_labels",
        "metric_snapshots", "posts", "raw_captures", "chat_sessions", "targets", "tenants",
    ):
        op.drop_table(t)
