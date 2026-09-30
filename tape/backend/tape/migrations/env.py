"""Środowisko Alembic dla Tape. Uruchamiane z CLI (`alembic upgrade head`) albo z make_sessionmaker."""

from alembic import context
from sqlalchemy import create_engine, pool, text

from tape.db import Base, import_models

import_models()                      # rejestruje wszystkie tabele w metadanych
config = context.config
target_metadata = Base.metadata


def _url() -> str:
    import os

    return config.get_main_option("sqlalchemy.url") or os.getenv("DATABASE_URL", "sqlite:///tape.db")


def run_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    connection = config.attributes.get("connection")
    if connection is None:
        engine = create_engine(_url(), poolclass=pool.NullPool)
        with engine.connect() as conn:
            _migrate(conn)
    else:
        _migrate(connection)


def _migrate(conn) -> None:
    postgres = conn.dialect.name == "postgresql"
    if postgres:
        # API i workery startują równolegle — tylko jeden proces naraz wykonuje migracje
        conn.execute(text("SELECT pg_advisory_lock(727301)"))
    try:
        context.configure(connection=conn, target_metadata=target_metadata,
                          render_as_batch=conn.dialect.name == "sqlite", compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    finally:
        if postgres:
            conn.execute(text("SELECT pg_advisory_unlock(727301)"))
            conn.commit()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
