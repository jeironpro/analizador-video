"""create all tables

Revision ID: 511e63184e42
Revises:
Create Date: 2026-07-29 09:06:09.844280

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "511e63184e42"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("code", sa.String(8), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("last_active", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "queue_items",
        sa.Column("temp_id", sa.String(36), primary_key=True),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("ext", sa.String(10), nullable=False),
        sa.Column("temp_path", sa.String(500), nullable=False),
        sa.Column("temp_filename", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), nullable=True, server_default=sa.text("'uploaded'")),
        sa.Column("logs", sa.Text(), nullable=True, server_default=sa.text("'[]'")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("session_id", sa.String(8), nullable=False, server_default=sa.text("'LEGACY01'")),
        sa.Column("retries", sa.Integer(), nullable=True, server_default=sa.text("0")),
    )
    op.create_table(
        "video",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("container", sa.String(20), nullable=True),
        sa.Column("mime_type", sa.String(100), nullable=True),
        sa.Column("analysis_result", sa.Text(), nullable=True),
        sa.Column("clamav_result", sa.String(50), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=True),
        sa.Column("session_id", sa.String(8), nullable=False, server_default=sa.text("'LEGACY01'")),
    )


def downgrade() -> None:
    op.drop_table("video")
    op.drop_table("queue_items")
    op.drop_table("sessions")
