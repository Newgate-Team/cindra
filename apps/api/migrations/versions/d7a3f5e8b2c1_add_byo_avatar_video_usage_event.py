"""add byo_avatar_video to usage_event_type

Revision ID: d7a3f5e8b2c1
Revises: b4e2a9c6d1f8
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d7a3f5e8b2c1"
down_revision: str | None = "b4e2a9c6d1f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Same autocommit + IF NOT EXISTS shape as c4f2a90b7e18 -- ALTER
    # TYPE ... ADD VALUE can't run inside Alembic's transaction.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE usage_event_type ADD VALUE IF NOT EXISTS 'byo_avatar_video'")


def downgrade() -> None:
    # Postgres can't drop an enum value; harmless once unreferenced.
    pass
