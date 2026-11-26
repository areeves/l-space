"""The SQLAlchemy tables must describe the same schema as the hand-written SQL.

This test exists only while both definitions do (phase 0.5, steps 1-4). When the hand-written
``SCHEMA`` is removed in step 5, so is this file.
"""

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import URL, create_engine

from l_space.library.store_sqlite import SCHEMA
from l_space.library.tables import metadata

BOOK = ("S1", "Title", "{}", 1, "")  # source_id, title, data, is_audiobook, category columns


def build_handwritten(path: Path) -> None:
    conn = sqlite3.connect(path)
    for statement in SCHEMA:
        conn.execute(statement)
    conn.commit()
    conn.close()


def build_from_metadata(path: Path) -> None:
    engine = create_engine(URL.create("sqlite", database=str(path)))
    metadata.create_all(engine)
    engine.dispose()


def describe(path: Path) -> dict:
    """Structure of every table: columns, indexes and foreign keys, as SQLite reports them."""
    conn = sqlite3.connect(path)
    out = {}
    names = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for table in sorted(names):
        # An INTEGER PRIMARY KEY is a rowid alias and can never be NULL, however it is declared.
        columns = [
            (name, ctype.upper(), 1 if notnull or pk else 0, default, pk)
            for _cid, name, ctype, notnull, default, pk in conn.execute(
                f"PRAGMA table_info({table})"
            )
        ]
        indexes = {}
        for _seq, name, unique, origin, _partial in conn.execute(f"PRAGMA index_list({table})"):
            indexed = [r[2] for r in conn.execute(f"PRAGMA index_info({name})")]
            indexes[name] = (bool(unique), origin, indexed)
        # (referenced table, from column, to column, on update, on delete)
        foreign_keys = sorted(
            (r[2], r[3], r[4], r[5], r[6]) for r in conn.execute(f"PRAGMA foreign_key_list({table})")
        )
        out[table] = {"columns": columns, "indexes": indexes, "foreign_keys": foreign_keys}
    conn.close()
    return out


@pytest.fixture
def metadata_db(tmp_path) -> Path:
    path = tmp_path / "metadata.db"
    build_from_metadata(path)
    return path


def test_metadata_describes_the_same_schema_as_the_handwritten_sql(tmp_path, metadata_db):
    handwritten = tmp_path / "handwritten.db"
    build_handwritten(handwritten)

    expected, actual = describe(handwritten), describe(metadata_db)

    assert sorted(actual) == sorted(expected) == [
        "book_people", "book_series", "book_tags", "books",
    ]
    for table in expected:
        assert actual[table] == expected[table], table


def test_books_keep_autoincrement_ids(metadata_db):
    sql = sqlite3.connect(metadata_db).execute(
        "SELECT sql FROM sqlite_master WHERE name = 'books'"
    ).fetchone()[0]
    assert "AUTOINCREMENT" in sql


def test_role_check_constraint_is_enforced(metadata_db):
    conn = sqlite3.connect(metadata_db)
    conn.execute(
        "INSERT INTO books (source_id, title, data, is_audiobook, parent_category_cf, "
        "child_category_cf) VALUES (?, ?, ?, ?, '', '')",
        BOOK[:4],
    )
    insert = "INSERT INTO book_people (book_id, role, name, name_cf) VALUES (1, ?, 'n', 'n')"
    conn.execute(insert, ("author",))
    conn.execute(insert, ("narrator",))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, ("publisher",))


def test_child_rows_cascade_when_foreign_keys_are_on(metadata_db):
    conn = sqlite3.connect(metadata_db)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT INTO books (source_id, title, data, is_audiobook, parent_category_cf, "
        "child_category_cf) VALUES (?, ?, ?, ?, '', '')",
        BOOK[:4],
    )
    conn.execute("INSERT INTO book_people VALUES (1, 'author', 'n', 'n')")
    conn.execute("INSERT INTO book_series VALUES (1, 's', 's', 1, NULL)")
    conn.execute("INSERT INTO book_tags VALUES (1, 't')")

    conn.execute("DELETE FROM books")

    for table in ("book_people", "book_series", "book_tags"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
