from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "43a3414f1996"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    is_pg = conn.dialect.name == "postgresql"
    j = "JSON" if is_pg else "TEXT"

    op.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            code VARCHAR(8) NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE,
            last_active TIMESTAMP WITHOUT TIME ZONE,
            PRIMARY KEY (code)
        )
    """)

    op.execute(f"""
        CREATE TABLE IF NOT EXISTS video (
            id VARCHAR(36) NOT NULL,
            filename VARCHAR(255) NOT NULL,
            original_name VARCHAR(255) NOT NULL,
            size INTEGER NOT NULL,
            container VARCHAR(20),
            mime_type VARCHAR(100),
            analysis_result {j},
            clamav_result VARCHAR(50),
            uploaded_at TIMESTAMP WITHOUT TIME ZONE,
            session_id VARCHAR(8) NOT NULL REFERENCES sessions(code),
            PRIMARY KEY (id)
        )
    """)

    op.execute(f"""
        CREATE TABLE IF NOT EXISTS queue_items (
            temp_id VARCHAR(36) NOT NULL,
            original_name VARCHAR(255) NOT NULL,
            ext VARCHAR(10) NOT NULL,
            temp_path VARCHAR(500) NOT NULL,
            temp_filename VARCHAR(500) NOT NULL,
            status VARCHAR(20) DEFAULT 'uploaded',
            logs {j} DEFAULT '[]',
            error TEXT,
            result {j},
            created_at TIMESTAMP WITHOUT TIME ZONE,
            session_id VARCHAR(8) NOT NULL REFERENCES sessions(code),
            retries INTEGER DEFAULT 0,
            PRIMARY KEY (temp_id)
        )
    """)

    if is_pg:
        op.execute("ALTER TABLE video ADD COLUMN IF NOT EXISTS sha256 VARCHAR(64)")
    else:
        try:
            op.execute("ALTER TABLE video ADD COLUMN sha256 VARCHAR(64)")
        except Exception:
            pass


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS queue_items")
    op.execute("DROP TABLE IF EXISTS video")
    op.execute("DROP TABLE IF EXISTS sessions")
