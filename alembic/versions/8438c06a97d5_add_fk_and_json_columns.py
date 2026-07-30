"""add FK constraints and JSON columns

Revision ID: 8438c06a97d5
Revises: 511e63184e42
Create Date: 2026-07-30 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8438c06a97d5"
down_revision: str | Sequence[str] | None = "511e63184e42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    is_sqlite = conn.dialect.name == "sqlite"

    op.create_foreign_key(
        "fk_video_session",
        "video",
        "sessions",
        ["session_id"],
        ["code"],
    )
    op.create_foreign_key(
        "fk_queue_items_session",
        "queue_items",
        "sessions",
        ["session_id"],
        ["code"],
    )

    if not is_sqlite:
        op.alter_column("video", "session_id", server_default=None)
        op.alter_column("queue_items", "session_id", server_default=None)

    op.alter_column("queue_items", "logs", type_=sa.JSON, existing_type=sa.Text, postgresql_using="logs::jsonb")
    op.alter_column("queue_items", "result", type_=sa.JSON, existing_type=sa.Text, postgresql_using="result::jsonb")


def downgrade() -> None:
    op.drop_constraint("fk_queue_items_session", "queue_items", type_="foreignkey")
    op.drop_constraint("fk_video_session", "video", type_="foreignkey")

    op.alter_column("queue_items", "result", type_=sa.Text, existing_type=sa.JSON)
    op.alter_column("queue_items", "logs", type_=sa.Text, existing_type=sa.JSON)

    conn = op.get_bind()
    is_sqlite = conn.dialect.name == "sqlite"
    if not is_sqlite:
        op.alter_column("video", "session_id", server_default=sa.text("'LEGACY01'"))
        op.alter_column("queue_items", "session_id", server_default=sa.text("'LEGACY01'"))
