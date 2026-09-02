"""add is_admin to users

Revision ID: b8e15d4c2f30
Revises: a3c07f21b95d
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8e15d4c2f30"
down_revision: str | None = "a3c07f21b95d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # CIN-147: staff flag for /metrics/summary. server_default keeps
    # existing rows valid under the NOT NULL constraint; it stays on the
    # column so hand-written INSERTs (this is set by hand in the DB)
    # don't have to name it.
    op.add_column(
        "users",
        sa.Column(
            "is_admin", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "is_admin")
