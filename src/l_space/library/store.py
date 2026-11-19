"""SQLite persistence. Exact questions (counts, filters, series status) are answered here,
with plain code, never by a language model."""

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from l_space.library.models import Book

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT,
    title TEXT NOT NULL,
    authors TEXT NOT NULL,
    narrators TEXT NOT NULL,
    series TEXT,
    series_position REAL,
    duration_minutes INTEGER,
    rating REAL,
    progress_pct REAL,
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_books_series ON books(series);
"""


class LibraryStore:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def replace_all(self, books: Iterable[Book]) -> int:
        """Re-import is idempotent: the library is replaced, not appended to."""
        with self.conn:
            self.conn.execute("DELETE FROM books")
            rows = [
                (
                    b.source_id, b.title, json.dumps(b.authors), json.dumps(b.narrators),
                    b.series, b.series_position, b.duration_minutes, b.rating,
                    b.progress_pct, json.dumps(b.raw),
                )
                for b in books
            ]
            self.conn.executemany(
                "INSERT INTO books (source_id, title, authors, narrators, series, series_position,"
                " duration_minutes, rating, progress_pct, raw) VALUES (?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
        return len(rows)

    def all_books(self) -> list[Book]:
        cur = self.conn.execute("SELECT * FROM books ORDER BY title")
        return [self._row_to_book(r) for r in cur.fetchall()]

    @staticmethod
    def _row_to_book(r: sqlite3.Row) -> Book:
        return Book(
            id=r["id"], source_id=r["source_id"], title=r["title"],
            authors=json.loads(r["authors"]), narrators=json.loads(r["narrators"]),
            series=r["series"], series_position=r["series_position"],
            duration_minutes=r["duration_minutes"], rating=r["rating"],
            progress_pct=r["progress_pct"], raw=json.loads(r["raw"]),
        )
