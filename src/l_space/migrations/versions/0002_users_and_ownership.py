"""Users, and every book belongs to one.

Creates ``users``, creates the default owner (named by ``config.attributes["owner_name"]``,
default ``owner``; no password, so nobody can log in until one is set) and gives it every
existing book. ``books.source_id`` stops being globally unique and becomes unique per user, and
every book gets a random ``public_id``.

SQLite cannot add NOT NULL columns or change constraints in place, so ``books`` is rebuilt
(batch mode, twice: add the nullable columns, backfill, then tighten). ``l_space.library.migrate``
runs this with foreign keys off, because dropping the old ``books`` table with them on would
cascade-delete every child row.

One-way: there is no downgrade. Back up the database first.

Revision ID: 0002
Revises: 0001
"""

import secrets
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMING_CONVENTION = {
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _public_id() -> str:
    # Frozen copy of the generator in store_sql.py: 96 random bits, URL-safe.
    return secrets.token_urlsafe(12)


def upgrade() -> None:
    conn = op.get_bind()
    owner = str(op.get_context().config.attributes.get("owner_name") or "owner").strip()
    if not owner:
        raise ValueError("The default owner needs a name")
    is_sqlite = conn.dialect.name == "sqlite"

    users = op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("username_cf", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_admin", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("disabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username_cf", name=op.f("uq_users_username_cf")),
        sqlite_autoincrement=True,
    )
    op.bulk_insert(
        users,
        [
            {
                "username": owner,
                "username_cf": owner.casefold(),
                "created_at": datetime.now(UTC),
                "is_admin": True,
                "disabled": False,
            }
        ],
    )
    owner_id = conn.execute(sa.text("SELECT id FROM users")).scalar_one()

    sequence = None
    if is_sqlite:  # keep AUTOINCREMENT from reusing the ids of deleted books
        sequence = conn.execute(sa.text("SELECT seq FROM sqlite_sequence WHERE name = 'books'")).scalar()

    table_kwargs = {"sqlite_autoincrement": True}
    with op.batch_alter_table(
        "books", table_kwargs=table_kwargs, naming_convention=NAMING_CONVENTION
    ) as batch:
        batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("public_id", sa.Text(), nullable=True))
        batch.drop_constraint("uq_books_source_id", type_="unique")

    conn.execute(sa.text("UPDATE books SET user_id = :owner"), {"owner": owner_id})
    ids = [row[0] for row in conn.execute(sa.text("SELECT id FROM books"))]
    if ids:  # an empty executemany is an error
        conn.execute(
            sa.text("UPDATE books SET public_id = :public_id WHERE id = :id"),
            [{"id": id_, "public_id": _public_id()} for id_ in ids],
        )

    with op.batch_alter_table(
        "books", table_kwargs=table_kwargs, naming_convention=NAMING_CONVENTION
    ) as batch:
        batch.alter_column("user_id", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("public_id", existing_type=sa.Text(), nullable=False)
        batch.create_foreign_key(
            "fk_books_user_id_users", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_unique_constraint("uq_books_user_source", ["user_id", "source_id"])
        batch.create_unique_constraint("uq_books_user_public", ["user_id", "public_id"])

    if sequence is not None:
        conn.execute(sa.text("UPDATE sqlite_sequence SET seq = :seq WHERE name = 'books'"), {"seq": sequence})


def downgrade() -> None:
    raise NotImplementedError("0002 is one-way: restore the backup taken before upgrading")
