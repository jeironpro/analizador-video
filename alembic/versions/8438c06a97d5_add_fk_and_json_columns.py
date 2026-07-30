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

    with op.batch_alter_table("video") as batch_op:
        batch_op.create_foreign_key("fk_video_session", "sessions", ["session_id"], ["code"])
        if not is_sqlite:
            batch_op.alter_column("session_id", server_default=None)

    with op.batch_alter_table("queue_items") as batch_op:
        batch_op.create_foreign_key("fk_queue_items_session", "sessions", ["session_id"], ["code"])
        batch_op.alter_column("logs", type_=sa.JSON, existing_type=sa.Text, postgresql_using="logs::jsonb")
        batch_op.alter_column(
            "result",
            type_=sa.JSON,
            existing_type=sa.Text,
            postgresql_using="result::jsonb",
        )
        if not is_sqlite:
            batch_op.alter_column("session_id", server_default=None)


def downgrade() -> None:
    conn = op.get_bind()
    is_sqlite = conn.dialect.name == "sqlite"

    with op.batch_alter_table("queue_items") as batch_op:
        batch_op.drop_constraint("fk_queue_items_session", type_="foreignkey")
        batch_op.alter_column("result", type_=sa.Text, existing_type=sa.JSON)
        batch_op.alter_column("logs", type_=sa.Text, existing_type=sa.JSON)
        if not is_sqlite:
            batch_op.alter_column("session_id", server_default=sa.text("'LEGACY01'"))

    with op.batch_alter_table("video") as batch_op:
        batch_op.drop_constraint("fk_video_session", type_="foreignkey")
        if not is_sqlite:
            batch_op.alter_column("session_id", server_default=sa.text("'LEGACY01'"))
