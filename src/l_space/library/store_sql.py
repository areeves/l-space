"""SQL persistence on SQLAlchemy Core. Exact questions (counts, filters, series status) are
answered here, with plain code, never by a language model.

``books.data`` (the full Book as JSON) is the source of truth. The other columns and the child
tables (defined in ``tables.py``) are a derived projection of it, built by ``index_rows`` and
nothing else, so they cannot drift from the blob. The store does not create or change the schema;
``migrate.py`` does.

Connection scope is one store call or one transaction, never longer (decision D8): the engine's
SQLite pool holds a single connection, so a call that kept it would block every other request.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import (
    Connection,
    Engine,
    Integer,
    Table,
    and_,
    delete,
    exists,
    func,
    insert,
    or_,
    select,
)
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.functions import FunctionElement

from l_space.library import queries, tables
from l_space.library.filters import BookFilter
from l_space.library.models import Book
from l_space.library.store import LibraryStore

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


class _Position(FunctionElement):
    """1-based position of ``needle`` in ``haystack``, 0 if absent; the dialect picks the function."""

    type = Integer()
    inherit_cache = True

    def __init__(self, haystack, needle):
        super().__init__(haystack, needle)


@compiles(_Position)
def _position_default(element, compiler, **kw):
    return f"instr({compiler.process(element.clauses, **kw)})"  # SQLite


@compiles(_Position, "postgresql")
def _position_postgresql(element, compiler, **kw):
    return f"strpos({compiler.process(element.clauses, **kw)})"


def contains(column: ColumnElement, text: str) -> ColumnElement[bool]:
    """``column`` contains the normalised ``text``.

    ``instr`` / ``strpos`` rather than LIKE, so ``%`` and ``_`` are ordinary characters and no
    escaping is needed. The column holds casefolded text and the needle is casefolded in Python.
    """
    return _Position(column, _needle(text)) > 0


def code_point_order(column: ColumnElement, dialect_name: str) -> ColumnElement:
    """Order by ``column`` in code-point order, which is what Python's ``sorted`` does.

    SQLite's default collation already is; Postgres follows the database locale unless told
    otherwise (decision D1).
    """
    return column.collate("C") if dialect_name == "postgresql" else column


def title_order(dialect_name: str) -> ColumnElement:
    return code_point_order(tables.books.c.title, dialect_name)


def build_where(f: BookFilter) -> ColumnElement[bool]:
    """Filter condition equivalent to ``queries.filter_books``, as a SQLAlchemy expression.

    Over the derived columns and child tables. The ``*_cf`` values were casefolded in Python when
    written, and search terms are casefolded with the same ``str.casefold``, so matching is
    identical to the pure-Python version (full Unicode).
    """
    books = tables.books

    def exists_in(table: Table, column: str, text: str, role: str | None = None):
        cond = [table.c.book_id == books.c.id, contains(table.c[column], text)]
        if role:
            cond.append(table.c.role == role)
        return exists().where(*cond)

    conditions: list[ColumnElement[bool]] = [books.c.is_audiobook == 1]
    if f.author:
        conditions.append(exists_in(tables.book_people, "name_cf", f.author, "author"))
    if f.narrator:
        conditions.append(exists_in(tables.book_people, "name_cf", f.narrator, "narrator"))
    if f.series:
        conditions.append(exists_in(tables.book_series, "name_cf", f.series))
    if f.category:
        # Parent and child are always present (stored '' when absent), so a whitespace-only term
        # matches every book, as it does in Python.
        conditions.append(
            or_(
                contains(books.c.parent_category_cf, f.category),
                contains(books.c.child_category_cf, f.category),
                exists_in(tables.book_tags, "tag_cf", f.category),
            )
        )
    # NULL never satisfies a comparison, so unknown length / rating never match, as in Python.
    if f.min_minutes is not None:
        conditions.append(books.c.duration_minutes >= f.min_minutes)
    if f.max_minutes is not None:
        conditions.append(books.c.duration_minutes <= f.max_minutes)
    if f.min_my_rating is not None:
        conditions.append(books.c.my_rating >= f.min_my_rating)
    return and_(*conditions)


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


class SqlLibraryStore(LibraryStore):
    def __init__(self, engine: Engine):
        """The tables must already exist: ``migrate(engine)`` creates and upgrades them."""
        self.engine = engine

    @classmethod
    def in_memory(cls, books: Iterable[Book] = ()) -> "SqlLibraryStore":
        """A throwaway migrated database holding ``books`` (for scripts such as the evals)."""
        from l_space.library.engine import create_db_engine
        from l_space.library.migrate import migrate

        engine = create_db_engine("sqlite://")
        migrate(engine)
        store = cls(engine)
        store.replace_all(books)
        return store

    def replace_all(self, books: Iterable[Book]) -> int:
        """Re-import is idempotent: the library is replaced, not appended to."""
        unique = {b.source_id: b for b in books}
        with self.engine.begin() as conn:
            conn.execute(delete(tables.books))  # child rows go with it (ON DELETE CASCADE)
            insert_books(conn, ((None, b) for b in unique.values()))
        return len(unique)

    def all_books(self) -> list[Book]:
        return self._select_books(None)

    def filter_books(self, f: BookFilter | None = None) -> list[Book]:
        """Filtering happens in SQL, so only matching rows are parsed into Books."""
        return self._select_books(build_where(f or BookFilter()))

    def _select_books(self, where: ColumnElement[bool] | None) -> list[Book]:
        query = select(tables.books.c.id, tables.books.c.data).order_by(
            title_order(self.engine.dialect.name)
        )
        if where is not None:
            query = query.where(where)
        with self.engine.connect() as conn:
            rows = conn.execute(query).all()
        # Parsed after the connection is released: CPU work never holds the pool's only slot.
        return [_to_book(id_, data) for id_, data in rows]

    def narrator_counts(self, f: BookFilter | None = None) -> list[tuple[str, int]]:
        return self._people_counts("narrator", f)

    def author_counts(self, f: BookFilter | None = None) -> list[tuple[str, int]]:
        return self._people_counts("author", f)

    def _people_counts(self, role: str, f: BookFilter | None) -> list[tuple[str, int]]:
        """Names with their number of matching books, most first, ties by name.

        Counts rows, so a name listed twice on one book counts twice, as ``Counter.update``
        does. Names are grouped exactly as written (not casefolded).
        """
        # An alias, so the filter's own EXISTS on book_people does not auto-correlate into it.
        people = tables.book_people.alias("counted")
        name = people.c.name
        count = func.count()
        query = (
            select(name, count)
            .select_from(people.join(tables.books, people.c.book_id == tables.books.c.id))
            .where(people.c.role == role, build_where(f or BookFilter()))
            .group_by(name)
            .order_by(count.desc(), code_point_order(name, self.engine.dialect.name))
        )
        with self.engine.connect() as conn:
            return [(n, c) for n, c in conn.execute(query)]

    def in_progress(self, f: BookFilter | None = None) -> list[Book]:
        where = and_(build_where(f or BookFilter()), tables.books.c.finished == 0)
        return self._select_books(where)

    def series_gaps(self) -> list[dict]:
        """Reads only the series rows, not the books' JSON."""
        series = tables.book_series
        query = select(series.c.name, series.c.position, series.c.position_end).where(
            series.c.position.is_not(None)
        )
        with self.engine.connect() as conn:
            entries = [tuple(row) for row in conn.execute(query)]
        return queries.gaps_from_entries(entries)

    def count_books(self, f: BookFilter | None = None) -> int:
        query = select(func.count()).select_from(tables.books).where(build_where(f or BookFilter()))
        with self.engine.connect() as conn:
            return conn.execute(query).scalar_one()
