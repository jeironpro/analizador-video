from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "3f7c1a9d2b4e"
down_revision: str | Sequence[str] | None = "43a3414f1996"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    is_pg = conn.dialect.name == "postgresql"

    for col in (
        ("duration", "FLOAT"),
        ("bitrate", "INTEGER"),
        ("has_thumbnail", "BOOLEAN"),
    ):
        name, dtype = col
        if is_pg:
            op.execute(f"ALTER TABLE video ADD COLUMN IF NOT EXISTS {name} {dtype}")
        else:
            try:
                op.execute(f"ALTER TABLE video ADD COLUMN {name} {dtype}")
            except Exception:
                pass


def downgrade() -> None:
    for name in ("duration", "bitrate", "has_thumbnail"):
        try:
            op.execute(f"ALTER TABLE video DROP COLUMN {name}")
        except Exception:
            pass
