"""add layout_render to usage_event_type

Revision ID: c4f2a90b7e18
Revises: b8e15d4c2f30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c4f2a90b7e18"
down_revision: str | None = "b8e15d4c2f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # CIN-148: counter for code-rendered template cards. Same
    # autocommit + IF NOT EXISTS shape as CIN-146's enum addition --
    # ALTER TYPE ... ADD VALUE can't run inside Alembic's transaction.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE usage_event_type ADD VALUE IF NOT EXISTS 'layout_render'")


def downgrade() -> None:
    # Postgres can't drop an enum value; harmless once unreferenced.
    pass
