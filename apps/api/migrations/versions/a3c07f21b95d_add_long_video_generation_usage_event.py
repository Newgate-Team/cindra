"""add long_video_generation to usage_event_type

Revision ID: a3c07f21b95d
Revises: f4a91c2d7e01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a3c07f21b95d"
down_revision: str | None = "f4a91c2d7e01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # CIN-146: separate counter for Seedance clips (CIN-144), which
    # cost ~15x a Veo one. ALTER TYPE ... ADD VALUE cannot run inside a
    # transaction block on older servers, and Alembic wraps migrations
    # in one -- IF NOT EXISTS plus an autocommit block keeps this
    # re-runnable and safe on every supported version.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE usage_event_type ADD VALUE IF NOT EXISTS 'long_video_generation'")


def downgrade() -> None:
    # Postgres cannot drop a value from an enum type. Rows carrying it
    # would have to be rewritten first, and the value is harmless when
    # the application no longer references it -- so this is a no-op
    # rather than a destructive rebuild of the type.
    pass
