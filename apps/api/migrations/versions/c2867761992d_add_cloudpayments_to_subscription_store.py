"""add cloudpayments to subscription_store

Revision ID: c2867761992d
Revises: 2671d4999def
Create Date: 2026-08-04 00:05:46.476713

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'c2867761992d'
down_revision: str | None = '2671d4999def'
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE subscription_store ADD VALUE 'cloudpayments'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE -- recreate the type
    # without 'cloudpayments' and swap the column over. Fails loudly
    # (as it should) if any row still has store='cloudpayments'.
    op.execute("ALTER TYPE subscription_store RENAME TO subscription_store_old")
    op.execute("CREATE TYPE subscription_store AS ENUM ('none', 'google_play', 'app_store')")
    op.execute(
        "ALTER TABLE subscriptions "
        "ALTER COLUMN store TYPE subscription_store "
        "USING store::text::subscription_store"
    )
    op.execute("DROP TYPE subscription_store_old")
