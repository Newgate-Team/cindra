"""twitch as an integration provider; stream option to post replies in chat

Revision ID: f2a8c6e1b9d4
Revises: e7b4c2a9d1f6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a8c6e1b9d4"
down_revision: str | None = "e7b4c2a9d1f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE integration_provider ADD VALUE IF NOT EXISTS 'twitch'")
    op.add_column(
        "live_streams",
        sa.Column("post_replies_to_chat", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("live_streams", "post_replies_to_chat")
    # integration_provider keeps 'twitch' -- Postgres can't drop an enum value.
