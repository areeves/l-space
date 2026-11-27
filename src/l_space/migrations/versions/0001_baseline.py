"""Baseline: the schema as of phase 0.5 (``books`` plus its derived child tables).

Written out by hand rather than from ``tables.metadata``: a migration must keep producing the
same schema after ``tables.py`` moves on. ``tests/test_migrate.py`` checks that head still
matches the metadata.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _fk_book_id(table: str) -> sa.Column:
    """``book_id`` pointing at ``books``; the child row goes with its book."""
    return sa.Column(
        "book_id",
        sa.Integer(),
        sa.ForeignKey("books.id", ondelete="CASCADE", name=op.f(f"fk_{table}_book_id_books")),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "books",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("data", sa.Text(), nullable=False),
        sa.Column("is_audiobook", sa.Integer(), nullable=False),
        sa.Column("format", sa.Text(), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("my_rating", sa.REAL(), nullable=True),
        sa.Column("finished", sa.Integer(), nullable=True),
        sa.Column("release_date", sa.Text(), nullable=True),
        sa.Column("parent_category_cf", sa.Text(), nullable=False),
        sa.Column("child_category_cf", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", name=op.f("uq_books_source_id")),
        sqlite_autoincrement=True,
    )
    op.create_index("idx_books_duration", "books", ["duration_minutes"])
    op.create_index("idx_books_my_rating", "books", ["my_rating"])

    op.create_table(
        "book_people",
        _fk_book_id("book_people"),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_cf", sa.Text(), nullable=False),
        sa.CheckConstraint("role IN ('author', 'narrator')", name=op.f("ck_book_people_role")),
    )
    op.create_index("idx_book_people_book", "book_people", ["book_id", "role"])

    op.create_table(
        "book_series",
        _fk_book_id("book_series"),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_cf", sa.Text(), nullable=False),
        sa.Column("position", sa.REAL(), nullable=True),
        sa.Column("position_end", sa.REAL(), nullable=True),
    )
    op.create_index("idx_book_series_book", "book_series", ["book_id"])

    op.create_table(
        "book_tags",
        _fk_book_id("book_tags"),
        sa.Column("tag_cf", sa.Text(), nullable=False),
    )
    op.create_index("idx_book_tags_book", "book_tags", ["book_id"])


def downgrade() -> None:
    for table in ("book_tags", "book_series", "book_people", "books"):
        op.drop_table(table)
