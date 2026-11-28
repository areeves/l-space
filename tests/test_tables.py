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


def insert_user(conn: sqlite3.Connection, name: str = "u") -> int:
    return conn.execute(
        "INSERT INTO users (username, username_cf, created_at) VALUES (?, ?, '2026-01-01')",
        (name, name),
    ).lastrowid


def insert_book(conn: sqlite3.Connection, user_id: int | None = None, source_id="S1", public_id="p1"):
    if user_id is None:
        user_id = conn.execute("SELECT id FROM users").fetchone() or (insert_user(conn),)
        user_id = user_id[0]
    conn.execute(
        "INSERT INTO books (source_id, title, data, is_audiobook, parent_category_cf, "
        "child_category_cf, user_id, public_id) VALUES (?, 'Title', '{}', 1, '', '', ?, ?)",
        (source_id, user_id, public_id),
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


def test_source_ids_are_unique_per_user_not_globally(metadata_db):
    conn = sqlite3.connect(metadata_db)
    alice, bob = insert_user(conn, "alice"), insert_user(conn, "bob")
    insert_book(conn, alice, "ASIN1", "a1")
    insert_book(conn, bob, "ASIN1", "b1")  # the same book in someone else's library
    with pytest.raises(sqlite3.IntegrityError):
        insert_book(conn, alice, "ASIN1", "a2")  # but not twice in one library


def test_public_ids_are_unique_per_user(metadata_db):
    conn = sqlite3.connect(metadata_db)
    alice, bob = insert_user(conn, "alice"), insert_user(conn, "bob")
    insert_book(conn, alice, "S1", "same")
    insert_book(conn, bob, "S1", "same")  # (user_id, public_id) is what is unique
    with pytest.raises(sqlite3.IntegrityError):
        insert_book(conn, alice, "S2", "same")


def test_usernames_are_unique_ignoring_case(metadata_db):
    conn = sqlite3.connect(metadata_db)
    insert_user(conn, "alice")
    with pytest.raises(sqlite3.IntegrityError):
        insert_user(conn, "alice")


def test_a_book_needs_an_owner_that_exists(metadata_db):
    conn = sqlite3.connect(metadata_db)
    conn.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        insert_book(conn, 999)


def test_deleting_a_user_removes_their_books_and_child_rows_only(metadata_db):
    conn = sqlite3.connect(metadata_db)
    conn.execute("PRAGMA foreign_keys = ON")
    alice, bob = insert_user(conn, "alice"), insert_user(conn, "bob")
    insert_book(conn, alice, "S1", "a1")
    insert_book(conn, bob, "S1", "b1")
    for book_id in (1, 2):
        conn.execute("INSERT INTO book_people VALUES (?, 'author', 'n', 'n')", (book_id,))
        conn.execute("INSERT INTO book_tags VALUES (?, 't')", (book_id,))

    conn.execute("DELETE FROM users WHERE id = ?", (alice,))

    assert conn.execute("SELECT user_id FROM books").fetchall() == [(bob,)]
    assert conn.execute("SELECT book_id FROM book_people").fetchall() == [(2,)]
    assert conn.execute("SELECT book_id FROM book_tags").fetchall() == [(2,)]
