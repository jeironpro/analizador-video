from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "246c6a88fa65"
down_revision = "8438c06a97d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("video") as batch_op:
        batch_op.add_column(sa.Column("sha256", sa.String(64)))


def downgrade() -> None:
    with op.batch_alter_table("video") as batch_op:
        batch_op.drop_column("sha256")
