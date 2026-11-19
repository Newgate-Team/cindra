"""create image_template_previews

Revision ID: d5b31e8ac402
Revises: c4f2a90b7e18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5b31e8ac402"
down_revision: str | None = "c4f2a90b7e18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # CIN-150: one stored example per AI image template. template_id is
    # the primary key -- regenerating a preview replaces the row rather
    # than accumulating history nobody reads.
    op.create_table(
        "image_template_previews",
        sa.Column("template_id", sa.String(length=50), primary_key=True),
        sa.Column("preview_url", sa.String(length=1024), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("image_template_previews")
