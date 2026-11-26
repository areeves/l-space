"""Compatibility alias (MULTI_USER_TODO.md, phase 0.5): the store is now the engine-based
``SqlLibraryStore`` in ``store_sql.py``. Removed in step 6 once nothing imports this name."""

from pathlib import Path

from sqlalchemy import URL

from l_space.library.engine import create_db_engine
from l_space.library.store_sql import SqlLibraryStore


class SqliteLibraryStore(SqlLibraryStore):
    """A ``SqlLibraryStore`` on its own SQLite engine for ``path`` (``":memory:"`` for a
    throwaway database)."""

    def __init__(self, path: Path | str):
        super().__init__(create_db_engine(URL.create("sqlite", database=str(path))))
