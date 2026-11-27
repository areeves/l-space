"""Stores for tests. The schema comes straight from ``tables.metadata`` (fast); the real
migration path is covered in test_migrate.py."""

from pathlib import Path

from sqlalchemy import URL

from l_space.library.engine import create_db_engine
from l_space.library.store_sql import SqlLibraryStore
from l_space.library.tables import metadata


def new_store(path: Path | str = ":memory:") -> SqlLibraryStore:
    """An empty store on its own SQLite engine (``":memory:"`` for a throwaway database)."""
    engine = create_db_engine(URL.create("sqlite", database=str(path)))
    metadata.create_all(engine)
    return SqlLibraryStore(engine)
