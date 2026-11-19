"""add share_generations_to_feed to users

Revision ID: bf045b5365a6
Revises: e4b875517b53
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "bf045b5365a6"
down_revision: str | None = "e4b875517b53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Лента (CIN-109) is a shared feed across all users by design --
    # for an agency that means an unreleased client campaign is visible
    # to every other user before the client sees it published.
    # server_default=true keeps existing rows valid under NOT NULL and
    # matches the new default for role=solo (the audience CIN-109 was
    # built for); the UPDATE below flips existing role=agency rows to
    # the opposite default going forward (register() already does this
    # for new signups, see app/routers/auth.py).
    op.add_column(
        "users",
        sa.Column(
            "share_generations_to_feed", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    op.execute("UPDATE users SET share_generations_to_feed = false WHERE role = 'agency'")


def downgrade() -> None:
    op.drop_column("users", "share_generations_to_feed")
