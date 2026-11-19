"""add youtube to social platform

Revision ID: f1a8c3e6b9d4
Revises: e9b1c4a7f3d2
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f1a8c3e6b9d4"
down_revision: str | None = "e9b1c4a7f3d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PostgreSQL enum additions are intentionally kept on downgrade --
    # removing one safely requires rebuilding the type and every
    # dependent column, while a harmless unused value preserves data
    # (same reasoning as 1e4f962cb0a7's TikTok addition).
    op.execute("ALTER TYPE social_platform ADD VALUE IF NOT EXISTS 'youtube'")


def downgrade() -> None:
    pass
