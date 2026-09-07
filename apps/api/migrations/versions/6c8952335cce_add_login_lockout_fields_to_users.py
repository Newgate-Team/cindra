"""add login lockout fields to users

Revision ID: 6c8952335cce
Revises: d5b31e8ac402
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6c8952335cce"
down_revision: str | None = "d5b31e8ac402"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # CIN-159: brute-force lockout on /auth/login. server_default keeps
    # existing rows valid under the NOT NULL constraint.
    op.add_column(
        "users",
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "users",
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_attempts")
