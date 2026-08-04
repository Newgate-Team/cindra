"""add role to users

Revision ID: 2ce891a0fe1f
Revises: d7fbfa539441
Create Date: 2026-07-31 20:24:34.551129

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '2ce891a0fe1f'
down_revision: str | None = 'd7fbfa539441'
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


user_role_enum = sa.Enum("agency", "solo", name="user_role")


def upgrade() -> None:
    user_role_enum.create(op.get_bind())
    op.add_column(
        "users",
        sa.Column("role", user_role_enum, nullable=False, server_default="solo"),
    )
    op.alter_column("users", "role", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "role")
    user_role_enum.drop(op.get_bind())
