"""simli as an integration provider

Revision ID: e7b4c2a9d1f6
Revises: c5e1a9d3f7b2
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e7b4c2a9d1f6"
down_revision: str | None = "c5e1a9d3f7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE integration_provider ADD VALUE IF NOT EXISTS 'simli'")


def downgrade() -> None:
    # Postgres can't drop an enum value; harmless once unreferenced.
    pass
