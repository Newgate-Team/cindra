"""add paypal to subscription_store

Revision ID: 6f723a1be96c
Revises: c2867761992d
Create Date: 2026-08-04 16:45:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '6f723a1be96c'
down_revision: str | None = 'c2867761992d'
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE subscription_store ADD VALUE 'paypal'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE -- recreate the type
    # without 'paypal' and swap the column over. Fails loudly (as it
    # should) if any row still has store='paypal'.
    op.execute("ALTER TYPE subscription_store RENAME TO subscription_store_old")
    op.execute(
        "CREATE TYPE subscription_store AS ENUM "
        "('none', 'google_play', 'app_store', 'cloudpayments')"
    )
    op.execute(
        "ALTER TABLE subscriptions "
        "ALTER COLUMN store TYPE subscription_store "
        "USING store::text::subscription_store"
    )
    op.execute("DROP TYPE subscription_store_old")
