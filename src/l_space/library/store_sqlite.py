"""SQLite persistence. Exact questions (counts, filters, series status) are answered here,
with plain code, never by a language model.

``books.data`` (the full Book as JSON) is the source of truth. The other columns and the child
tables are a derived projection of it, built by ``index_rows`` and nothing else, so they cannot
drift from the blob. Changing that projection means bumping ``SCHEMA_VERSION``: on open, a
database at another version is rebuilt from ``data`` (no re-import of the CSV needed).
"""

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from l_space.library.filters import BookFilter
from l_space.library.models import NON_BOOK_FORMATS, Book
from l_space.library.store import LibraryStore

SCHEMA_VERSION = 2

# Derived scalar columns of ``books``, in the order ``IndexRows.values`` supplies them.
INDEX_COLUMNS = (
    "is_audiobook",
    "format",
    "duration_minutes",
    "my_rating",
    "finished",
    "release_date",
    "parent_category_cf",
    "child_category_cf",
)

# Child tables hold one row per list item. ``*_cf`` columns are casefolded in Python when the
# row is written, with the same ``str.casefold`` the pure-Python queries use. Duplicates are kept
# (a book listing an author twice counts twice, as ``Counter.update`` does).
SCHEMA = (
    """
    CREATE TABLE books (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        data TEXT NOT NULL,  -- the full Book as JSON, including the untouched source row
        is_audiobook INTEGER NOT NULL,
        format TEXT,
        duration_minutes INTEGER,
        my_rating REAL,
        finished INTEGER,  -- 1 / 0 / NULL (unknown)
        release_date TEXT,  -- ISO 8601
        parent_category_cf TEXT NOT NULL,  -- '' when absent, like ``or ""`` in filter_books
        child_category_cf TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_books_duration ON books (duration_minutes)",
    "CREATE INDEX idx_books_my_rating ON books (my_rating)",
    """
    CREATE TABLE book_people (
        book_id INTEGER NOT NULL REFERENCES books (id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK (role IN ('author', 'narrator')),
        name TEXT NOT NULL,
        name_cf TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_book_people_book ON book_people (book_id, role)",
    """
    CREATE TABLE book_series (
        book_id INTEGER NOT NULL REFERENCES books (id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        name_cf TEXT NOT NULL,
        position REAL,
        position_end REAL
    )
    """,
    "CREATE INDEX idx_book_series_book ON book_series (book_id)",
    """
    CREATE TABLE book_tags (
        book_id INTEGER NOT NULL REFERENCES books (id) ON DELETE CASCADE,
        tag_cf TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_book_tags_book ON book_tags (book_id)",
)
DROP_ALL = tuple(
    f"DROP TABLE IF EXISTS {t}" for t in ("book_tags", "book_series", "book_people", "books")
)


@dataclass(frozen=True)
class IndexRows:
    """Everything derived from one Book for querying."""

    values: tuple  # one value per INDEX_COLUMNS entry
    people: list[tuple[str, str, str]]  # (role, name, name_cf)
    series: list[tuple[str, str, float | None, float | None]]  # (name, name_cf, pos, pos_end)
    tags: list[tuple[str]]  # (tag_cf,)


def index_rows(book: Book) -> IndexRows:
    """The single definition of the derived columns and child rows for a book."""
    finished = None if book.finished is None else int(book.finished)
    return IndexRows(
        values=(
            int(book.is_audiobook),
            book.format,
            book.duration_minutes,
            book.my_rating,
            finished,
            book.release_date.isoformat() if book.release_date else None,
            (book.parent_category or "").casefold(),
            (book.child_category or "").casefold(),
        ),
        people=[
            *(("author", n, n.casefold()) for n in book.authors),
            *(("narrator", n, n.casefold()) for n in book.narrators),
        ],
        series=[(s.name, s.name.casefold(), s.position, s.position_end) for s in book.series],
        tags=[(t.casefold(),) for t in book.tags],
    )


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
        self.conn.execute("PRAGMA foreign_keys = ON")  # per connection; makes cascades work
        self.conn.create_function(
            "casefold", 1, lambda v: v.casefold() if isinstance(v, str) else None, deterministic=True
        )
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create the schema, or rebuild the derived columns from ``data`` if it is out of date."""
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            return
        has_books = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'books'"
        ).fetchone()
        old = (
            [(id_, _to_book(id_, data)) for id_, data in self.conn.execute("SELECT id, data FROM books")]
            if has_books
            else []
        )
        # One explicit transaction, so an interrupted rebuild leaves the old database intact.
        self.conn.execute("BEGIN")
        try:
            for statement in (*DROP_ALL, *SCHEMA):
                self.conn.execute(statement)
            self._insert(old)
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def _insert(self, rows: Iterable[tuple[int | None, Book]]) -> None:
        """Write books (``id`` None = assign one) with their derived columns and child rows."""
        names = ("id", "source_id", "title", "data", *INDEX_COLUMNS)
        sql = f"INSERT INTO books ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})"
        for id_, book in rows:
            ix = index_rows(book)
            # The id lives in the column, not the blob, so a rebuild reproduces `data` exactly.
            blob = book.model_copy(update={"id": None}).model_dump_json()
            book_id = self.conn.execute(
                sql, (id_, book.source_id, book.title, blob, *ix.values)
            ).lastrowid
            self.conn.executemany(
                "INSERT INTO book_people (book_id, role, name, name_cf) VALUES (?, ?, ?, ?)",
                [(book_id, *p) for p in ix.people],
            )
            self.conn.executemany(
                "INSERT INTO book_series (book_id, name, name_cf, position, position_end)"
                " VALUES (?, ?, ?, ?, ?)",
                [(book_id, *s) for s in ix.series],
            )
            self.conn.executemany(
                "INSERT INTO book_tags (book_id, tag_cf) VALUES (?, ?)",
                [(book_id, *t) for t in ix.tags],
            )

    def replace_all(self, books: Iterable[Book]) -> int:
        """Re-import is idempotent: the library is replaced, not appended to."""
        unique = {b.source_id: b for b in books}
        with self.conn:
            self.conn.execute("DELETE FROM books")  # child rows go with it (ON DELETE CASCADE)
            self._insert((None, b) for b in unique.values())
        return len(unique)

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
