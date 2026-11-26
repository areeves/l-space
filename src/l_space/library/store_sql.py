"""SQL persistence on SQLAlchemy Core. Exact questions (counts, filters, series status) are
answered here, with plain code, never by a language model.

``books.data`` (the full Book as JSON) is the source of truth. The other columns and the child
tables (defined in ``tables.py``) are a derived projection of it, built by ``index_rows`` and
nothing else, so they cannot drift from the blob. Changing that projection means bumping
``SCHEMA_VERSION``: on open, a database at another version is rebuilt from ``data`` (no re-import
of the CSV needed). Phase 0.5 step 5 replaces this version check with Alembic.

Connection scope is one store call or one transaction, never longer (decision D8): the engine's
SQLite pool holds a single connection, so a call that kept it would block every other request.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import Connection, Engine, delete, insert, select

from l_space.library import tables
from l_space.library.filters import BookFilter
from l_space.library.models import Book
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


def _needle(text: str) -> str:
    """Normalise a search term exactly as ``queries._contains`` does."""
    return text.casefold().strip()


def build_where(f: BookFilter) -> tuple[str, list]:
    """WHERE clause (and parameters) equivalent to ``queries.filter_books``.

    Plain SQL over the derived columns and child tables. The ``*_cf`` values were casefolded in
    Python when written, and search terms are casefolded here with the same ``str.casefold``, so
    matching is identical to the pure-Python version (full Unicode, and ``instr`` rather than
    ``LIKE`` so ``%`` and ``_`` are ordinary characters).
    """
    clauses = ["is_audiobook = 1"]
    params: list = []

    def exists_in(table: str, column: str, text: str, role: str | None = None) -> str:
        sql = f"SELECT 1 FROM {table} WHERE book_id = books.id"
        if role:
            sql += " AND role = ?"
            params.append(role)
        sql += f" AND instr({column}, ?) > 0"
        params.append(_needle(text))
        return f"EXISTS ({sql})"

    if f.author:
        clauses.append(exists_in("book_people", "name_cf", f.author, "author"))
    if f.narrator:
        clauses.append(exists_in("book_people", "name_cf", f.narrator, "narrator"))
    if f.series:
        clauses.append(exists_in("book_series", "name_cf", f.series))
    if f.category:
        # Parent and child are always present (stored '' when absent), so a whitespace-only term
        # matches every book, as it does in Python.
        params.extend([_needle(f.category)] * 2)  # the two instr terms come first
        tags = exists_in("book_tags", "tag_cf", f.category)  # then its parameter
        clauses.append(
            f"(instr(parent_category_cf, ?) > 0 OR instr(child_category_cf, ?) > 0 OR {tags})"
        )
    # NULL never satisfies a comparison, so unknown length / rating never match, as in Python.
    if f.min_minutes is not None:
        clauses.append("duration_minutes >= ?")
        params.append(f.min_minutes)
    if f.max_minutes is not None:
        clauses.append("duration_minutes <= ?")
        params.append(f.max_minutes)
    if f.min_my_rating is not None:
        clauses.append("my_rating >= ?")
        params.append(f.min_my_rating)
    return " AND ".join(clauses), params


def _to_book(id_: int, data: str) -> Book:
    return Book.model_validate_json(data).model_copy(update={"id": id_})


def insert_books(conn: Connection, rows: Iterable[tuple[int | None, Book]]) -> None:
    """Write books (``id`` None = assign one) with their derived columns and child rows."""
    for id_, book in rows:
        ix = index_rows(book)
        # The id lives in the column, not the blob, so a rebuild reproduces `data` exactly.
        blob = book.model_copy(update={"id": None}).model_dump_json()
        values = {
            "source_id": book.source_id,
            "title": book.title,
            "data": blob,
            **dict(zip(INDEX_COLUMNS, ix.values, strict=True)),
        }
        if id_ is not None:
            values["id"] = id_
        book_id = conn.execute(insert(tables.books).values(**values)).inserted_primary_key[0]
        if ix.people:
            conn.execute(
                insert(tables.book_people),
                [
                    {"book_id": book_id, "role": role, "name": name, "name_cf": name_cf}
                    for role, name, name_cf in ix.people
                ],
            )
        if ix.series:
            conn.execute(
                insert(tables.book_series),
                [
                    {
                        "book_id": book_id,
                        "name": name,
                        "name_cf": name_cf,
                        "position": position,
                        "position_end": position_end,
                    }
                    for name, name_cf, position, position_end in ix.series
                ],
            )
        if ix.tags:
            conn.execute(
                insert(tables.book_tags),
                [{"book_id": book_id, "tag_cf": tag_cf} for (tag_cf,) in ix.tags],
            )


def ensure_schema(engine: Engine) -> None:
    """Create the schema, or rebuild the derived columns from ``data`` if it is out of date."""
    # One transaction: the engine emits BEGIN itself (see engine.py), so the DDL below is part
    # of it and an interrupted rebuild leaves the old database intact.
    with engine.begin() as conn:
        version = conn.exec_driver_sql("PRAGMA user_version").scalar()
        if version == SCHEMA_VERSION:
            return
        has_books = conn.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'books'"
        ).first()
        old = (
            [(id_, _to_book(id_, data)) for id_, data in conn.exec_driver_sql(
                "SELECT id, data FROM books"
            )]
            if has_books
            else []
        )
        tables.metadata.drop_all(conn)
        tables.metadata.create_all(conn)
        insert_books(conn, old)
        conn.exec_driver_sql(f"PRAGMA user_version = {SCHEMA_VERSION}")


class SqlLibraryStore(LibraryStore):
    def __init__(self, engine: Engine):
        self.engine = engine
        ensure_schema(engine)

    def replace_all(self, books: Iterable[Book]) -> int:
        """Re-import is idempotent: the library is replaced, not appended to."""
        unique = {b.source_id: b for b in books}
        with self.engine.begin() as conn:
            conn.execute(delete(tables.books))  # child rows go with it (ON DELETE CASCADE)
            insert_books(conn, ((None, b) for b in unique.values()))
        return len(unique)

    def all_books(self) -> list[Book]:
        query = select(tables.books.c.id, tables.books.c.data).order_by(tables.books.c.title)
        with self.engine.connect() as conn:
            rows = conn.execute(query).all()
        # Parsed after the connection is released: CPU work never holds the pool's only slot.
        return [_to_book(id_, data) for id_, data in rows]

    def filter_books(self, f: BookFilter | None = None) -> list[Book]:
        """Filtering happens in SQL, so only matching rows are parsed into Books."""
        where, params = build_where(f or BookFilter())
        with self.engine.connect() as conn:
            rows = conn.exec_driver_sql(
                f"SELECT id, data FROM books WHERE {where} ORDER BY title", tuple(params)
            ).all()
        return [_to_book(id_, data) for id_, data in rows]

    def count_books(self, f: BookFilter | None = None) -> int:
        where, params = build_where(f or BookFilter())
        with self.engine.connect() as conn:
            return conn.exec_driver_sql(
                f"SELECT COUNT(*) FROM books WHERE {where}", tuple(params)
            ).scalar_one()
