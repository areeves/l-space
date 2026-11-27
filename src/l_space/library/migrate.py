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
from sqlalchemy import Connection, Engine, inspect

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


def migrate(engine: Engine) -> None:
    """Bring the database to the latest revision (a no-op when it already is)."""
    with engine.begin() as conn:
        config = alembic_config(conn)
        names = set(inspect(conn).get_table_names())
        if "books" in names and "alembic_version" not in names:
            _adopt_unversioned(conn, config)
        command.upgrade(config, "head")


def prepare_database(settings: Settings) -> None:
    """Migrate the database, or (``auto_migrate`` off) insist that it already is."""
    engine = get_engine(settings)
    if settings.auto_migrate:
        migrate(engine)
    elif needs_migration(engine):
        raise RuntimeError("The database schema is out of date. Run: l-space migrate")


def _adopt_unversioned(conn: Connection, config) -> None:
    """Move a pre-Alembic database onto the baseline without losing or re-importing anything.

    Uses the current ``insert_books``, which is right while head is the baseline. When a later
    revision changes ``books``, this step must write the baseline shape itself (or be retired).
    """
    old = [
        (id_, _to_book(id_, data))
        for id_, data in conn.exec_driver_sql("SELECT id, data FROM books ORDER BY id")
    ]
    tables.metadata.drop_all(conn)
    command.upgrade(config, BASELINE)
    insert_books(conn, old)
