import os
from logging.config import fileConfig

import alembic.context
import sqlalchemy as sa

config = alembic.context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def get_url() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:///videos.db")
    if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
        _db_path = url[len("sqlite:///") :]
        if not os.path.isabs(_db_path):
            _parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            _db_path = os.path.abspath(os.path.join(_parent, "instance", _db_path))
            url = f"sqlite:///{_db_path}"
    if url.startswith("postgres") and "sslmode" not in url and os.environ.get("RENDER"):
        url += "?sslmode=require" if "?" not in url else "&sslmode=require"
    return url


def run_migrations_offline():
    alembic.context.configure(url=get_url(), target_metadata=None, literal_binds=True)
    with alembic.context.begin_transaction():
        alembic.context.run_migrations()


def run_migrations_online():
    engine = sa.create_engine(get_url())
    with engine.connect() as connection:
        alembic.context.configure(connection=connection, target_metadata=None)
        with alembic.context.begin_transaction():
            alembic.context.run_migrations()


if alembic.context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
