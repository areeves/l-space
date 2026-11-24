"""SQLite persistence. Exact questions (counts, filters, series status) are answered here,
with plain code, never by a language model."""

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from l_space.library.filters import BookFilter
from l_space.library.models import NON_BOOK_FORMATS, Book
from l_space.library.store import LibraryStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    data TEXT NOT NULL  -- the full Book as JSON, including the untouched source row
);
"""


def _json(path: str) -> str:
    return f"json_extract(data, '{path}')"


def _contains_any(needle: str, values_sql: str) -> tuple[str, list]:
    """SQL for ``queries._contains``: casefolded substring test against a value expression."""
    return f"instr(casefold({values_sql}), ?) > 0", [needle.casefold().strip()]


def build_where(f: BookFilter) -> tuple[str, list]:
    """WHERE clause (and parameters) equivalent to ``queries.filter_books``.

    Everything lives in the ``data`` JSON column, so fields are read with json_extract and the
    list fields with json_each. ``casefold`` is a Python function registered on the connection,
    which keeps matching identical to the pure-Python version (full Unicode, no LIKE wildcards).
    """
    # A missing format counts as an audiobook, so test with COALESCE: NOT IN on NULL is NULL.
    marks = ", ".join("?" for _ in NON_BOOK_FORMATS)
    clauses = [f"COALESCE({_json('$.format')}, '') NOT IN ({marks})"]
    params: list = sorted(NON_BOOK_FORMATS)

    def each(path: str, member: str | None, needle: str) -> None:
        value = "value" if member is None else f"json_extract(value, '{member}')"
        test, p = _contains_any(needle, value)
        clauses.append(f"EXISTS (SELECT 1 FROM json_each(data, '{path}') WHERE {test})")
        params.extend(p)

    if f.author:
        each("$.authors", None, f.author)
    if f.narrator:
        each("$.narrators", None, f.narrator)
    if f.series:
        each("$.series", "$.name", f.series)
    if f.category:
        needle = f.category.casefold().strip()
        tags = "EXISTS (SELECT 1 FROM json_each(data, '$.tags') WHERE instr(casefold(value), ?) > 0)"
        clauses.append(
            "(instr(casefold(COALESCE(" + _json("$.parent_category") + ", '')), ?) > 0"
            " OR instr(casefold(COALESCE(" + _json("$.child_category") + ", '')), ?) > 0"
            f" OR {tags})"
        )
        params.extend([needle, needle, needle])
    # NULL never satisfies a comparison, so unknown length / rating never match, as in Python.
    if f.min_minutes is not None:
        clauses.append(f"{_json('$.duration_minutes')} >= ?")
        params.append(f.min_minutes)
    if f.max_minutes is not None:
        clauses.append(f"{_json('$.duration_minutes')} <= ?")
        params.append(f.max_minutes)
    if f.min_my_rating is not None:
        clauses.append(f"{_json('$.my_rating')} >= ?")
        params.append(f.min_my_rating)
    return " AND ".join(clauses), params


def _to_book(id_: int, data: str) -> Book:
    return Book.model_validate_json(data).model_copy(update={"id": id_})


class SqliteLibraryStore(LibraryStore):
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.create_function(
            "casefold", 1, lambda v: v.casefold() if isinstance(v, str) else None, deterministic=True
        )
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
        return [_to_book(id_, data) for id_, data in cur]

    def filter_books(self, f: BookFilter | None = None) -> list[Book]:
        """Filtering happens in SQL, so only matching rows are parsed into Books."""
        where, params = build_where(f or BookFilter())
        cur = self.conn.execute(
            f"SELECT id, data FROM books WHERE {where} ORDER BY title", params
        )
        return [_to_book(id_, data) for id_, data in cur]

    def count_books(self, f: BookFilter | None = None) -> int:
        where, params = build_where(f or BookFilter())
        return self.conn.execute(f"SELECT COUNT(*) FROM books WHERE {where}", params).fetchone()[0]
