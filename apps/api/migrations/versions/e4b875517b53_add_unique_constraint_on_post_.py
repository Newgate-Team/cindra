"""add unique constraint on post generation job and account

Revision ID: e4b875517b53
Revises: 6c8952335cce
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e4b875517b53"
down_revision: str | None = "6c8952335cce"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Closes a real race in routers/posts.py::create_post's CIN-122
    # idempotency check (concurrent retries could double-charge and
    # double-publish -- see that function's comment). NULL is distinct
    # from NULL under a UNIQUE constraint in Postgres, so this only
    # ever constrains generated posts (generation_job_id IS NOT NULL);
    # manually-composed posts (NULL) are unaffected.
    #
    # WARNING before running against a real database: if the exact race
    # this migration closes has already fired in production, there may
    # already be duplicate (generation_job_id, social_account_id) rows
    # -- this ADD CONSTRAINT fails outright against existing duplicates.
    # Check for them first:
    #   SELECT generation_job_id, social_account_id, count(*)
    #   FROM posts WHERE generation_job_id IS NOT NULL
    #   GROUP BY generation_job_id, social_account_id HAVING count(*) > 1;
    # and deduplicate manually if any turn up before upgrading.
    op.create_unique_constraint(
        "uq_post_generation_job_account", "posts", ["generation_job_id", "social_account_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_post_generation_job_account", "posts", type_="unique")
