"""create ab tests

Revision ID: c7a4f0d8e5b1
Revises: a3d9c1e7f2b4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c7a4f0d8e5b1"
down_revision: str | None = "a3d9c1e7f2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ab_tests first -- its winner_generation_job_id FK needs
    # generation_jobs to already exist (it does, this is an old
    # table), and generation_jobs.ab_test_id (added below) needs
    # ab_tests to already exist. Circular-looking, but not actually a
    # cycle at DDL time given this order.
    op.create_table(
        "ab_tests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("topic", sa.String(length=5000), nullable=False),
        sa.Column(
            "winner_generation_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generation_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ab_tests_user_id", "ab_tests", ["user_id"])

    op.add_column(
        "generation_jobs",
        sa.Column(
            "ab_test_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ab_tests.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_generation_jobs_ab_test_id", "generation_jobs", ["ab_test_id"])


def downgrade() -> None:
    op.drop_index("ix_generation_jobs_ab_test_id", table_name="generation_jobs")
    op.drop_column("generation_jobs", "ab_test_id")
    op.drop_index("ix_ab_tests_user_id", table_name="ab_tests")
    op.drop_table("ab_tests")
