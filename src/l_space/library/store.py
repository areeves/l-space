"""SQLite persistence. Exact questions (counts, filters, series status) are answered here,
with plain code, never by a language model."""

import sqlite3
from abc import ABC, abstractmethod
from collections.abc import Iterable
from pathlib import Path

from l_space.core.settings import Settings
from l_space.library.models import Book

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    data TEXT NOT NULL  -- the full Book as JSON, including the untouched source row
);
"""


class LibraryStore(ABC):
    @classmethod
    def from_settings(cls, settings: Settings) -> "LibraryStore":
        return SqliteLibraryStore(settings.db_path)

    @abstractmethod
    def replace_all(self, books: Iterable[Book]) -> int:
        """Replace the whole library with `books` (re-import is idempotent) and return the count."""

    @abstractmethod
    def all_books(self) -> list[Book]:
        """Return every book, ordered by title."""


class SqliteLibraryStore(LibraryStore):
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.executescript(SCHEMA)

    def replace_all(self, books: Iterable[Book]) -> int:
        """Re-import is idempotent: the library is replaced, not appended to."""
        rows = {b.source_id: (b.source_id, b.title, b.model_dump_json()) for b in books}
        with self.conn:
            self.conn.execute("DELETE FROM books")
            self.conn.executemany(
                "INSERT INTO books (source_id, title, data) VALUES (?, ?, ?)", rows.values()
            )
        return len(rows)

    def all_books(self) -> list[Book]:
        cur = self.conn.execute("SELECT id, data FROM books ORDER BY title")
        return [Book.model_validate_json(data).model_copy(update={"id": id_}) for id_, data in cur]
