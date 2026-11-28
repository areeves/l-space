"""Indexes that lead with ``user_id``.

Every book query is restricted to one user, so an index on ``duration_minutes`` alone no longer
helps; ``(user_id, duration_minutes)`` does, and ``(user_id, title)`` also returns a user's books
in title order without a sort.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("idx_books_duration", table_name="books")
    op.drop_index("idx_books_my_rating", table_name="books")
    op.create_index("idx_books_user_title", "books", ["user_id", "title"])
    op.create_index("idx_books_user_duration", "books", ["user_id", "duration_minutes"])
    op.create_index("idx_books_user_my_rating", "books", ["user_id", "my_rating"])


def downgrade() -> None:
    op.drop_index("idx_books_user_my_rating", table_name="books")
    op.drop_index("idx_books_user_duration", table_name="books")
    op.drop_index("idx_books_user_title", table_name="books")
    op.create_index("idx_books_my_rating", "books", ["my_rating"])
    op.create_index("idx_books_duration", "books", ["duration_minutes"])
