"""create video_projects table

Revision ID: 8b1f0d3c55aa
Revises: 1e4f962cb0a7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8b1f0d3c55aa"
down_revision: str | None = "1e4f962cb0a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "video_projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("topic", sa.String(length=5000), nullable=False),
        sa.Column("brand_guide", sa.String(length=5000), nullable=True),
        sa.Column("script", sa.String(length=20000), nullable=True),
        sa.Column("style", sa.String(length=50), nullable=True),
        sa.Column("brief_files", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("video_url", sa.String(length=2048), nullable=True),
        sa.Column(
            "video_generation_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generation_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_video_projects_user_id", "video_projects", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_video_projects_user_id", table_name="video_projects")
    op.drop_table("video_projects")
