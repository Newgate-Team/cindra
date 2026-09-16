"""add linkedin to social platform

Revision ID: a2d7f4b1c8e3
Revises: f1a8c3e6b9d4
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a2d7f4b1c8e3"
down_revision: str | None = "f1a8c3e6b9d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PostgreSQL enum additions are intentionally kept on downgrade --
    # same reasoning as f1a8c3e6b9d4's YouTube addition.
    op.execute("ALTER TYPE social_platform ADD VALUE IF NOT EXISTS 'linkedin'")


def downgrade() -> None:
    pass
