"""add TikTok platform and per-platform post options

Revision ID: 1e4f962cb0a7
Revises: 2677d6aeb403
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "1e4f962cb0a7"
down_revision: str | None = "2677d6aeb403"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PostgreSQL enum additions are intentionally kept on downgrade:
    # removing one safely requires rebuilding the type and every
    # dependent column, while a harmless unused value preserves data.
    op.execute("ALTER TYPE social_platform ADD VALUE IF NOT EXISTS 'tiktok'")
    op.add_column(
        "posts",
        sa.Column(
            "platform_options",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("posts", "platform_options")
