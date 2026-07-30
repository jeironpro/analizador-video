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

    def _create_fk_if_not_exists(table, name, referent, local_cols, remote_cols):
        if is_sqlite:
            with op.batch_alter_table(table) as b:
                b.create_foreign_key(name, referent, local_cols, remote_cols)
        else:
            exists = conn.execute(
                sa.text("SELECT 1 FROM pg_constraint WHERE conname = :name"),
                {"name": name},
            ).scalar()
            if not exists:
                op.create_foreign_key(name, table, referent, local_cols, remote_cols)

    _create_fk_if_not_exists("video", "fk_video_session", "sessions", ["session_id"], ["code"])
    if not is_sqlite:
        op.execute("ALTER TABLE video ALTER COLUMN session_id DROP DEFAULT")

    if not is_sqlite:
        op.execute("ALTER TABLE queue_items ALTER COLUMN logs DROP DEFAULT")
        op.execute("ALTER TABLE queue_items ALTER COLUMN result DROP DEFAULT")

    with op.batch_alter_table("queue_items") as batch_op:
        batch_op.alter_column("logs", type_=sa.JSON, existing_type=sa.Text, postgresql_using="logs::jsonb")
        batch_op.alter_column(
            "result",
            type_=sa.JSON,
            existing_type=sa.Text,
            postgresql_using="result::jsonb",
        )

    _create_fk_if_not_exists("queue_items", "fk_queue_items_session", "sessions", ["session_id"], ["code"])
    if not is_sqlite:
        op.execute("ALTER TABLE queue_items ALTER COLUMN session_id DROP DEFAULT")


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
