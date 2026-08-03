"""add facebook to social_platform

Revision ID: dc760703de20
Revises: 6c10a752a1ee
Create Date: 2026-08-03 21:50:31.577621

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'dc760703de20'
down_revision: str | None = '6c10a752a1ee'
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Postgres enums only gain values via ALTER TYPE ... ADD VALUE --
    # there's no op.* helper for it. Safe inside Alembic's transaction
    # on Postgres 12+ as long as the new value isn't used in the same
    # transaction (it isn't here). See 6c10a752a1ee for the same
    # pattern applied to subscription_tier.
    op.execute("ALTER TYPE social_platform ADD VALUE 'facebook'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE -- the standard
    # workaround is to recreate the type without 'facebook' and swap
    # the column over. Fails loudly (as it should) if any row still
    # has platform='facebook' at downgrade time.
    op.execute("ALTER TYPE social_platform RENAME TO social_platform_old")
    op.execute("CREATE TYPE social_platform AS ENUM ('telegram', 'instagram')")
    op.execute(
        "ALTER TABLE social_accounts "
        "ALTER COLUMN platform TYPE social_platform "
        "USING platform::text::social_platform"
    )
    op.execute("DROP TYPE social_platform_old")
