"""add content_kind to posts

Revision ID: 2671d4999def
Revises: dc760703de20
Create Date: 2026-08-03 23:24:27.325855

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '2671d4999def'
down_revision: str | None = 'dc760703de20'
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "posts",
        sa.Column("content_kind", sa.String(length=50), nullable=False, server_default="post"),
    )


def downgrade() -> None:
    op.drop_column("posts", "content_kind")
