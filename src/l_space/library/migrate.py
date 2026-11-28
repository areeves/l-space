"""Schema migrations (Alembic), run from code.

The app and ``l-space migrate`` call ``migrate(engine)``: it brings any database to the latest
revision inside one transaction (the engine emits ``BEGIN`` itself, so SQLite DDL rolls back
with it). The store never creates or alters tables; ``tables.py`` says what the schema should
be, ``migrations/versions`` how a database gets there.

A database written before Alembic (tables but no ``alembic_version``) is adopted once: its
books are read from ``data`` (the source of truth), the old tables are dropped, the baseline is
created, and the books are written back with their ids and blobs unchanged. That one path covers
every earlier release, and it leaves the database with exactly the baseline's schema (including
the named constraints that batch migrations on SQLite rely on) rather than a lookalike.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, inspect, select

from l_space import migrations
from l_space.core.settings import Settings
from l_space.library import tables
from l_space.library.engine import get_engine
from l_space.library.store_sql import _to_book, insert_books

BASELINE = "0001"


def alembic_config(connection: Connection | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(migrations.__file__).parent))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def current_revision(engine: Engine) -> str | None:
    """The revision the database is at, or None if it has never been migrated."""
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def needs_migration(engine: Engine) -> bool:
    return current_revision(engine) != head_revision()


def migrate(engine: Engine, owner: str | None = None) -> None:
    """Bring the database to the latest revision (a no-op when it already is).

    ``owner`` names the account that takes over an existing library when the database first
    gets users (migration 0002); it is ignored once that has happened.
    """
    with engine.connect() as conn:
        raw = conn.connection.dbapi_connection
        sqlite = engine.dialect.name == "sqlite"
        # SQLite rebuilds tables to change them, and dropping a parent table with foreign keys on
        # would cascade-delete its children. The pragma only works outside a transaction.
        foreign_keys = raw.execute("PRAGMA foreign_keys").fetchone()[0] if sqlite else None
        if sqlite:
            raw.execute("PRAGMA foreign_keys = OFF")
        try:
            with conn.begin():
                config = alembic_config(conn)
                config.attributes["owner_name"] = owner
                names = set(inspect(conn).get_table_names())
                if "books" in names and "alembic_version" not in names:
                    _adopt_unversioned(conn, config)
                command.upgrade(config, "head")
                if sqlite:
                    broken = conn.exec_driver_sql("PRAGMA foreign_key_check").all()
                    if broken:
                        raise RuntimeError(f"Migration left broken foreign keys: {broken[:3]}")
        finally:
            if sqlite:
                raw.execute(f"PRAGMA foreign_keys = {foreign_keys}")


def prepare_database(settings: Settings) -> None:
    """Migrate the database, or (``auto_migrate`` off) insist that it already is."""
    engine = get_engine(settings)
    if settings.auto_migrate:
        migrate(engine, settings.default_owner)
    elif needs_migration(engine):
        raise RuntimeError("The database schema is out of date. Run: l-space migrate")


def _adopt_unversioned(conn: Connection, config) -> None:
    """Move a pre-Alembic database to the current schema without losing or re-importing anything.

    The books are read from ``data``, the old tables dropped, the current schema created by the
    migrations (which also creates the default owner) and the books written back to it, keeping
    their ids. Because it writes with the current ``insert_books`` into the head schema, later
    revisions need no special handling here.
    """
    old = [
        (id_, _to_book(id_, data))
        for id_, data in conn.exec_driver_sql("SELECT id, data FROM books ORDER BY id")
    ]
    tables.metadata.drop_all(conn)
    command.upgrade(config, "head")
    owner_id = conn.execute(select(tables.users.c.id)).scalar_one()
    insert_books(conn, old, owner_id)
