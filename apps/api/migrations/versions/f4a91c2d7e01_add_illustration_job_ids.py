"""add illustration_job_ids to video_projects

Revision ID: f4a91c2d7e01
Revises: 8b1f0d3c55aa
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f4a91c2d7e01"
down_revision: str | None = "8b1f0d3c55aa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "video_projects",
        sa.Column("illustration_job_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("video_projects", "illustration_job_ids")
