"""Migracje muszą odpowiadać modelom — inaczej produkcja dostanie inny schemat niż testy."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from tape.db import Base, import_models, make_sessionmaker, migrate


def test_migrations_match_models(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    migrate(url)
    import_models()
    engine = create_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == [], f"modele różnią się od migracji — wygeneruj nową: alembic revision --autogenerate\n{diff}"


def test_upgrade_is_idempotent_and_versioned(tmp_path):
    url = f"sqlite:///{tmp_path / 'i.db'}"
    make_sessionmaker(url)
    make_sessionmaker(url)                                        # drugi start: nic do zrobienia, bez błędu
    tables = set(inspect(create_engine(url)).get_table_names())
    assert {"fills", "alembic_version", "prop_accounts", "user_settings"} <= tables
