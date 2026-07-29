from logging.config import fileConfig
import os

from sqlalchemy import engine_from_config, pool

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from models import db
target_metadata = db.metadata


def run_migrations_offline():
    url = os.environ.get("DATABASE_URL", "sqlite:///videos.db")
    if url.startswith("postgres") and "sslmode" not in url:
        url += "?sslmode=require" if "?" not in url else "&sslmode=require"
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    from app import app
    with app.app_context():
        connectable = db.engine
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
            )
            with context.begin_transaction():
                context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
