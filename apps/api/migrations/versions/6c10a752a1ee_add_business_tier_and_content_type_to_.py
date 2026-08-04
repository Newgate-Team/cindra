"""add business tier and content_type to usage_events

Revision ID: 6c10a752a1ee
Revises: 78dca0802cae
Create Date: 2026-08-02 17:15:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '6c10a752a1ee'
down_revision: str | None = '78dca0802cae'
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Postgres enums only gain values via ALTER TYPE ... ADD VALUE --
    # there's no op.* helper for it. Safe inside Alembic's transaction
    # on Postgres 12+ as long as the new value isn't used in the same
    # transaction (it isn't here).
    op.execute("ALTER TYPE subscription_tier ADD VALUE 'business'")

    # Reuses the generation_content_type enum already created by
    # 8fc915e52133 -- create_type=False marks this as a reference to
    # an existing type rather than a new one, so SQLAlchemy neither
    # tries to CREATE TYPE here nor DROP TYPE on downgrade (that type
    # still backs generation_jobs.content_type either way).
    op.add_column(
        'usage_events',
        sa.Column(
            'content_type',
            postgresql.ENUM(
                'text', 'image', 'video',
                name='generation_content_type',
                create_type=False,
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('usage_events', 'content_type')

    # Postgres has no ALTER TYPE ... DROP VALUE -- the standard
    # workaround is to recreate the type without 'business' and swap
    # the column over. Fails loudly (as it should) if any row still
    # has tier='business' at downgrade time.
    op.execute("ALTER TYPE subscription_tier RENAME TO subscription_tier_old")
    op.execute("CREATE TYPE subscription_tier AS ENUM ('free', 'pro')")
    op.execute(
        "ALTER TABLE subscriptions "
        "ALTER COLUMN tier TYPE subscription_tier "
        "USING tier::text::subscription_tier"
    )
    op.execute("DROP TYPE subscription_tier_old")
