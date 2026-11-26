"""Behaviour of the schema defined in ``tables.py`` (constraints SQLite enforces)."""

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import URL, create_engine

from l_space.library.tables import metadata


@pytest.fixture
def metadata_db(tmp_path) -> Path:
    path = tmp_path / "metadata.db"
    engine = create_engine(URL.create("sqlite", database=str(path)))
    metadata.create_all(engine)
    engine.dispose()
    return path


def insert_book(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO books (source_id, title, data, is_audiobook, parent_category_cf, "
        "child_category_cf) VALUES ('S1', 'Title', '{}', 1, '', '')"
    )


def test_books_keep_autoincrement_ids(metadata_db):
    sql = sqlite3.connect(metadata_db).execute(
        "SELECT sql FROM sqlite_master WHERE name = 'books'"
    ).fetchone()[0]
    assert "AUTOINCREMENT" in sql


def test_role_check_constraint_is_enforced(metadata_db):
    conn = sqlite3.connect(metadata_db)
    insert_book(conn)
    insert = "INSERT INTO book_people (book_id, role, name, name_cf) VALUES (1, ?, 'n', 'n')"
    conn.execute(insert, ("author",))
    conn.execute(insert, ("narrator",))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, ("publisher",))


def test_child_rows_cascade_when_foreign_keys_are_on(metadata_db):
    conn = sqlite3.connect(metadata_db)
    conn.execute("PRAGMA foreign_keys = ON")
    insert_book(conn)
    conn.execute("INSERT INTO book_people VALUES (1, 'author', 'n', 'n')")
    conn.execute("INSERT INTO book_series VALUES (1, 's', 's', 1, NULL)")
    conn.execute("INSERT INTO book_tags VALUES (1, 't')")

    conn.execute("DELETE FROM books")

    for table in ("book_people", "book_series", "book_tags"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
