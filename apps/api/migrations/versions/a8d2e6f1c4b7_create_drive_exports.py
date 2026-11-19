"""google drive integration and drive exports

Revision ID: a8d2e6f1c4b7
Revises: f3c8b1d5a7e9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a8d2e6f1c4b7"
down_revision: str | None = "f3c8b1d5a7e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE integration_provider ADD VALUE IF NOT EXISTS 'google_drive'")

    op.add_column(
        "integrations",
        sa.Column(
            "settings",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.create_table(
        "drive_exports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("integrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_url", sa.String(length=1024), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum("queued", "processing", "completed", "failed", name="drive_export_status"),
            nullable=False,
        ),
        sa.Column("upload_session_url", sa.String(length=2048), nullable=True),
        sa.Column("drive_file_id", sa.String(length=255), nullable=True),
        sa.Column("web_view_link", sa.String(length=1024), nullable=True),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_drive_exports_user_id", "drive_exports", ["user_id"])
    op.create_index("ix_drive_exports_integration_id", "drive_exports", ["integration_id"])
    op.create_index("ix_drive_exports_status", "drive_exports", ["status"])
    op.create_index(
        "uq_drive_export_in_flight",
        "drive_exports",
        ["integration_id", "source_url"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'processing')"),
    )


def downgrade() -> None:
    op.drop_index("uq_drive_export_in_flight", table_name="drive_exports")
    op.drop_index("ix_drive_exports_status", table_name="drive_exports")
    op.drop_index("ix_drive_exports_integration_id", table_name="drive_exports")
    op.drop_index("ix_drive_exports_user_id", table_name="drive_exports")
    op.drop_table("drive_exports")
    sa.Enum(name="drive_export_status").drop(op.get_bind())
    op.drop_column("integrations", "settings")
    # integration_provider keeps 'google_drive' -- Postgres can't drop an
    # enum value; harmless once unreferenced.
