"""The database schema as SQLAlchemy ``Table`` objects: one definition of what is stored.

``books.data`` (the full Book as JSON) is the source of truth. Every other column and the child
tables are a derived projection of it, produced by ``store_sql.index_rows`` and nothing else.
Child ``*_cf`` columns are casefolded in Python when written, with the same ``str.casefold`` the
pure-Python queries use, so SQL matching cannot disagree with them.

Ownership: every book belongs to one user (``books.user_id``), and each user owns their own
copy of every book (decision D2), so ``source_id`` is unique per user, not globally. Child tables
point at their book and inherit its owner. ``public_id`` is the opaque identifier the API will
use instead of the integer ``id`` (decision D5).

The store (``store_sql.py``) fills these tables; the Alembic migrations in ``l_space/migrations``
create them, and ``tests/test_migrate.py`` checks the two agree.
"""

from sqlalchemy import (
    REAL,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    false,
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


# One row per account. ``username`` is shown as typed; ``username_cf`` (casefolded) is what logins
# match and what must be unique. A NULL ``password_hash`` means the account cannot log in yet.
users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("username", Text, nullable=False),
    Column("username_cf", Text, nullable=False, unique=True),
    Column("password_hash", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("is_admin", Boolean, nullable=False, server_default=false()),
    Column("disabled", Boolean, nullable=False, server_default=false()),
    sqlite_autoincrement=True,
)

# REAL is SQLite's 8-byte float; on Postgres it is a 4-byte float, which holds ratings and
# series positions (halves and whole numbers) exactly.
books = Table(
    "books",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source_id", Text, nullable=False),
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
    # Added by migration 0002, so last in column order.
    Column("user_id", Integer, nullable=False),
    Column("public_id", Text, nullable=False),
    ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    UniqueConstraint("user_id", "source_id", name="uq_books_user_source"),
    UniqueConstraint("user_id", "public_id", name="uq_books_user_public"),
    # Every query filters on user_id first, so the indexes lead with it. (user_id, title) also
    # serves ORDER BY title without a sort.
    Index("idx_books_user_title", "user_id", "title"),
    Index("idx_books_user_duration", "user_id", "duration_minutes"),
    Index("idx_books_user_my_rating", "user_id", "my_rating"),
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
