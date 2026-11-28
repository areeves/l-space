"""Stores for tests. The schema comes straight from ``tables.metadata`` (fast); the real
migration path is covered in test_migrate.py."""

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import URL, insert, select

from l_space.library.engine import create_db_engine
from l_space.library.store_sql import SqlLibraryStore
from l_space.library.tables import metadata, users


def new_store(path: Path | str = ":memory:") -> SqlLibraryStore:
    """An empty store on its own SQLite engine (``":memory:"`` for a throwaway database)."""
    engine = create_db_engine(URL.create("sqlite", database=str(path)))
    metadata.create_all(engine)
    with engine.begin() as conn:  # what migration 0002 creates
        if conn.execute(select(users.c.id)).first() is None:
            conn.execute(
                insert(users).values(
                    username="owner", username_cf="owner", created_at=datetime.now(UTC), is_admin=True
                )
            )
    return SqlLibraryStore(engine)
