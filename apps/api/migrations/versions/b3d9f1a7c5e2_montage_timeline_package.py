"""montage: timeline-only jobs and the downloadable timeline package

Revision ID: b3d9f1a7c5e2
Revises: f2a8c6e1b9d4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3d9f1a7c5e2"
down_revision: str | None = "f2a8c6e1b9d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "montage_jobs",
        sa.Column("render", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column("montage_jobs", sa.Column("package_status", sa.String(20), nullable=True))
    op.add_column("montage_jobs", sa.Column("package_url", sa.String(1024), nullable=True))
    op.add_column("montage_jobs", sa.Column("package_error", sa.String(500), nullable=True))
    op.add_column(
        "montage_jobs",
        sa.Column("package_requested_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    for column in ("package_requested_at", "package_error", "package_url", "package_status", "render"):
        op.drop_column("montage_jobs", column)
