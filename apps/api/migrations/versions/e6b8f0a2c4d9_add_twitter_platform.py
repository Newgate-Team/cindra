"""add twitter to social platform

Revision ID: e6b8f0a2c4d9
Revises: c9e5a1f3d7b2
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e6b8f0a2c4d9"
down_revision: str | None = "c9e5a1f3d7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PostgreSQL enum additions are intentionally kept on downgrade --
    # same reasoning as f1a8c3e6b9d4/a2d7f4b1c8e3/c9e5a1f3d7b2's own
    # YouTube/LinkedIn/Reddit additions.
    op.execute("ALTER TYPE social_platform ADD VALUE IF NOT EXISTS 'twitter'")


def downgrade() -> None:
    pass
