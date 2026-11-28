"""Stores for tests. The schema comes straight from ``tables.metadata`` (fast); the real
migration path is covered in test_migrate.py."""

from pathlib import Path

from sqlalchemy import URL

from l_space.library.engine import create_db_engine
from l_space.library.store_sql import SqlLibraryStore, SqlUserLibrary
from l_space.library.tables import metadata


def new_store(path: Path | str = ":memory:") -> SqlLibraryStore:
    """A store on its own SQLite engine, with the default owner migration 0002 would create."""
    engine = create_db_engine(URL.create("sqlite", database=str(path)))
    metadata.create_all(engine)
    store = SqlLibraryStore(engine)
    if store.find_user("owner") is None:
        store.create_user("owner", is_admin=True)
    return store


def new_library(path: Path | str = ":memory:") -> SqlUserLibrary:
    """The default owner's (empty) library: ``":memory:"`` is a throwaway database."""
    store = new_store(path)
    return store.for_user(store.default_owner().id)
