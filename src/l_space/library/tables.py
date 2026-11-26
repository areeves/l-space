"""The database schema as SQLAlchemy ``Table`` objects: one definition of what is stored.

``books.data`` (the full Book as JSON) is the source of truth. Every other column and the child
tables are a derived projection of it, produced by ``store_sql.index_rows`` and nothing else.
Child ``*_cf`` columns are casefolded in Python when written, with the same ``str.casefold`` the
pure-Python queries use, so SQL matching cannot disagree with them.

The store (``store_sql.py``) creates and fills these tables; the Alembic migrations (phase 0.5,
step 5) will be built on them too.
"""

from sqlalchemy import (
    REAL,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
)

# Stable constraint names: Alembic's batch mode on SQLite needs them to alter tables.
NAMING_CONVENTION = {
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _book_id() -> Column:
    """Child tables point at their book and go with it (ON DELETE CASCADE)."""
    return Column("book_id", Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False)


# REAL is SQLite's 8-byte float; on Postgres it is a 4-byte float, which holds ratings and
# series positions (halves and whole numbers) exactly.
books = Table(
    "books",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source_id", Text, nullable=False, unique=True),
    Column("title", Text, nullable=False),
    Column("data", Text, nullable=False),  # the full Book as JSON, including the source row
    Column("is_audiobook", Integer, nullable=False),
    Column("format", Text),
    Column("duration_minutes", Integer),
    Column("my_rating", REAL),
    Column("finished", Integer),  # 1 / 0 / NULL (unknown)
    Column("release_date", Text),  # ISO 8601
    Column("parent_category_cf", Text, nullable=False),  # '' when absent, like `or ""` in Python
    Column("child_category_cf", Text, nullable=False),
    Index("idx_books_duration", "duration_minutes"),
    Index("idx_books_my_rating", "my_rating"),
    sqlite_autoincrement=True,
)

# One row per author or narrator. Duplicates are kept (a book listing an author twice counts
# twice, as ``Counter.update`` does).
book_people = Table(
    "book_people",
    metadata,
    _book_id(),
    Column("role", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("name_cf", Text, nullable=False),
    CheckConstraint("role IN ('author', 'narrator')", name="role"),
    Index("idx_book_people_book", "book_id", "role"),
)

book_series = Table(
    "book_series",
    metadata,
    _book_id(),
    Column("name", Text, nullable=False),
    Column("name_cf", Text, nullable=False),
    Column("position", REAL),
    Column("position_end", REAL),
    Index("idx_book_series_book", "book_id"),
)

book_tags = Table(
    "book_tags",
    metadata,
    _book_id(),
    Column("tag_cf", Text, nullable=False),
    Index("idx_book_tags_book", "book_id"),
)
