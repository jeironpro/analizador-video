"""create all tables

Revision ID: 511e63184e42
Revises:
Create Date: 2026-07-29 09:06:09.844280

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "511e63184e42"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _if_not_exists = "CREATE TABLE IF NOT EXISTS"
    op.execute(f"""
        {_if_not_exists} sessions (
            code VARCHAR(8) NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE,
            last_active TIMESTAMP WITHOUT TIME ZONE,
            PRIMARY KEY (code)
        )
    """)
    op.execute(f"""
        {_if_not_exists} queue_items (
            temp_id VARCHAR(36) NOT NULL,
            original_name VARCHAR(255) NOT NULL,
            ext VARCHAR(10) NOT NULL,
            temp_path VARCHAR(500) NOT NULL,
            temp_filename VARCHAR(500) NOT NULL,
            status VARCHAR(20) DEFAULT 'uploaded',
            logs TEXT DEFAULT '[]',
            error TEXT,
            result TEXT,
            created_at TIMESTAMP WITHOUT TIME ZONE,
            session_id VARCHAR(8) NOT NULL DEFAULT 'LEGACY01',
            retries INTEGER DEFAULT 0,
            PRIMARY KEY (temp_id)
        )
    """)
    op.execute(f"""
        {_if_not_exists} video (
            id VARCHAR(36) NOT NULL,
            filename VARCHAR(255) NOT NULL,
            original_name VARCHAR(255) NOT NULL,
            size INTEGER NOT NULL,
            container VARCHAR(20),
            mime_type VARCHAR(100),
            analysis_result TEXT,
            clamav_result VARCHAR(50),
            uploaded_at TIMESTAMP WITHOUT TIME ZONE,
            session_id VARCHAR(8) NOT NULL DEFAULT 'LEGACY01',
            PRIMARY KEY (id)
        )
    """)


def downgrade() -> None:
    op.drop_table("video")
    op.drop_table("queue_items")
    op.drop_table("sessions")
