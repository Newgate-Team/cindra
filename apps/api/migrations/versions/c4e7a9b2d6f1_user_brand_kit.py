"""users: brand kit (default brand guide and tone)

Revision ID: c4e7a9b2d6f1
Revises: b3d9f1a7c5e2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e7a9b2d6f1"
down_revision: str | None = "b3d9f1a7c5e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("brand_guide", sa.String(5000), nullable=True))
    op.add_column("users", sa.Column("default_tone", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "default_tone")
    op.drop_column("users", "brand_guide")
