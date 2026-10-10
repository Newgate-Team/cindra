"""create integrations

Revision ID: b4e2a9c6d1f8
Revises: e6b8f0a2c4d9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b4e2a9c6d1f8"
down_revision: str | None = "e6b8f0a2c4d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "provider", sa.Enum("heygen", name="integration_provider"), nullable=False
        ),
        sa.Column("encrypted_secret", sa.String(length=2048), nullable=False),
        sa.Column("account_label", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "provider", name="uq_integration_user_provider"),
    )


def downgrade() -> None:
    op.drop_table("integrations")
    sa.Enum(name="integration_provider").drop(op.get_bind())
