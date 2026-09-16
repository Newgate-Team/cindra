"""add reddit to social platform

Revision ID: c9e5a1f3d7b2
Revises: a2d7f4b1c8e3
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c9e5a1f3d7b2"
down_revision: str | None = "a2d7f4b1c8e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PostgreSQL enum additions are intentionally kept on downgrade --
    # same reasoning as f1a8c3e6b9d4's YouTube and a2d7f4b1c8e3's
    # LinkedIn additions.
    op.execute("ALTER TYPE social_platform ADD VALUE IF NOT EXISTS 'reddit'")


def downgrade() -> None:
    pass
