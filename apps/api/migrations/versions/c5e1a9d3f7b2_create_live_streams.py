"""live avatar streams and their event log

Revision ID: c5e1a9d3f7b2
Revises: a8d2e6f1c4b7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c5e1a9d3f7b2"
down_revision: str | None = "a8d2e6f1c4b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE usage_event_type ADD VALUE IF NOT EXISTS 'stream_hour'")
        op.execute("ALTER TYPE integration_provider ADD VALUE IF NOT EXISTS 'liveavatar'")

    op.create_table(
        "live_streams",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("platform", sa.Enum("twitch", name="stream_platform"), nullable=False),
        sa.Column("channel", sa.String(length=100), nullable=False),
        sa.Column("encrypted_stream_key", sa.String(length=1024), nullable=True),
        sa.Column("persona", sa.String(length=4000), nullable=False),
        sa.Column("idle_topics", sa.String(length=4000), nullable=False),
        sa.Column("blocked_terms", postgresql.JSONB(), nullable=False),
        sa.Column("avatar_provider", sa.String(length=40), nullable=False),
        sa.Column("avatar", postgresql.JSONB(), nullable=False),
        sa.Column("min_reply_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("max_hours", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("draft", "queued", "live", "ended", "failed", name="stream_status"),
            nullable=False,
        ),
        sa.Column("stop_requested", sa.Boolean(), nullable=False),
        sa.Column("agent_name", sa.String(length=120), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hours_billed", sa.Integer(), nullable=False),
        sa.Column("stats", postgresql.JSONB(), nullable=False),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_live_streams_user_id", "live_streams", ["user_id"])
    op.create_index("ix_live_streams_status", "live_streams", ["status"])

    op.create_table(
        "stream_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "stream_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("live_streams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("author", sa.String(length=100), nullable=True),
        sa.Column("message", sa.String(length=600), nullable=True),
        sa.Column("text", sa.String(length=2000), nullable=False),
        sa.Column("emotion", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_stream_events_stream_id", "stream_events", ["stream_id"])
    op.create_index("ix_stream_events_created_at", "stream_events", ["created_at"])

    op.create_table(
        "stream_agents",
        sa.Column("name", sa.String(length=120), primary_key=True),
        sa.Column("version", sa.String(length=60), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("stream_agents")
    op.drop_index("ix_stream_events_created_at", table_name="stream_events")
    op.drop_index("ix_stream_events_stream_id", table_name="stream_events")
    op.drop_table("stream_events")
    op.drop_index("ix_live_streams_status", table_name="live_streams")
    op.drop_index("ix_live_streams_user_id", table_name="live_streams")
    op.drop_table("live_streams")
    sa.Enum(name="stream_status").drop(op.get_bind())
    sa.Enum(name="stream_platform").drop(op.get_bind())
    # usage_event_type keeps 'stream_hour' and integration_provider keeps
    # 'liveavatar' -- Postgres can't drop an enum value; harmless once
    # unreferenced.
