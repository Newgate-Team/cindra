"""make hashed_password nullable for google sign-in

Revision ID: 2677d6aeb403
Revises: e71eff5e2e59
Create Date: 2026-08-19 15:56:33.108252

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '2677d6aeb403'
down_revision: str | None = 'e71eff5e2e59'
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "users", "hashed_password", existing_type=sa.String(length=255), nullable=True
    )


def downgrade() -> None:
    # Google-created accounts have NULL here -- give them an
    # impossible-to-verify placeholder so the NOT NULL constraint can
    # be restored without deleting users.
    op.execute("UPDATE users SET hashed_password = '!google-account' WHERE hashed_password IS NULL")
    op.alter_column(
        "users", "hashed_password", existing_type=sa.String(length=255), nullable=False
    )
