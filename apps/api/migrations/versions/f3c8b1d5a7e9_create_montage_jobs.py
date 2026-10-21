"""create montage jobs and agents

Revision ID: f3c8b1d5a7e9
Revises: d7a3f5e8b2c1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3c8b1d5a7e9"
down_revision: str | None = "d7a3f5e8b2c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE usage_event_type ADD VALUE IF NOT EXISTS 'davinci_montage'")

    op.create_table(
        "montage_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("queued", "processing", "completed", "failed", name="montage_status"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("stage", sa.String(length=200), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("result_video_url", sa.String(length=1024), nullable=True),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        sa.Column("agent_name", sa.String(length=120), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_montage_jobs_user_id", "montage_jobs", ["user_id"])
    op.create_index("ix_montage_jobs_status", "montage_jobs", ["status"])

    op.create_table(
        "montage_agents",
        sa.Column("name", sa.String(length=120), primary_key=True),
        sa.Column("version", sa.String(length=60), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("montage_agents")
    op.drop_index("ix_montage_jobs_status", table_name="montage_jobs")
    op.drop_index("ix_montage_jobs_user_id", table_name="montage_jobs")
    op.drop_table("montage_jobs")
    sa.Enum(name="montage_status").drop(op.get_bind())
    # usage_event_type keeps 'davinci_montage' -- Postgres can't drop an
    # enum value; harmless once unreferenced.
