"""The derived columns and child tables must always agree with the ``data`` blob."""

import sqlite3
from pathlib import Path

import pytest

from awkward import EXTRA
from l_space.ingestion.loaders import load_ale_csv
from l_space.library.store_sqlite import (
    INDEX_COLUMNS,
    SCHEMA_VERSION,
    SqliteLibraryStore,
    _to_book,
    index_rows,
)

SAMPLE = Path(__file__).parent.parent / "data" / "sample" / "library.csv"
CHILD_TABLES = ("book_people", "book_series", "book_tags")


def make_store(path=":memory:") -> SqliteLibraryStore:
    store = SqliteLibraryStore(path)
    store.replace_all([*load_ale_csv(SAMPLE), *EXTRA])
    return store


def child_rows(store: SqliteLibraryStore, table: str, book_id: int) -> list:
    cols = {"book_people": "role, name, name_cf", "book_series": "name, name_cf, position, position_end",
            "book_tags": "tag_cf"}[table]
    return sorted(
        store.conn.execute(f"SELECT {cols} FROM {table} WHERE book_id = ?", (book_id,)).fetchall(),
        key=repr,
    )


def assert_index_matches_blob(store: SqliteLibraryStore) -> int:
    """Every row's columns and child rows equal what ``index_rows`` derives from its blob."""
    checked = 0
    for id_, data in store.conn.execute("SELECT id, data FROM books").fetchall():
        ix = index_rows(_to_book(id_, data))
        row = store.conn.execute(
            f"SELECT {', '.join(INDEX_COLUMNS)} FROM books WHERE id = ?", (id_,)
        ).fetchone()
        assert row == ix.values
        assert child_rows(store, "book_people", id_) == sorted(ix.people, key=repr)
        assert child_rows(store, "book_series", id_) == sorted(ix.series, key=repr)
        assert child_rows(store, "book_tags", id_) == sorted(ix.tags, key=repr)
        checked += 1
    return checked


def test_columns_and_child_rows_match_the_blob():
    store = make_store()
    assert assert_index_matches_blob(store) == len(store.all_books()) > 12


def test_casefolding_happens_at_write_time():
    store = make_store()
    names = store.conn.execute(
        "SELECT name, name_cf FROM book_people WHERE name LIKE 'Łukasz%' LIMIT 1"
    ).fetchone()
    assert names == ("Łukasz Żółć", "łukasz żółć")
    assert store.conn.execute(
        "SELECT 1 FROM book_series WHERE name_cf = 'strasse saga'"
    ).fetchone()
    assert store.conn.execute("SELECT is_audiobook FROM books WHERE title = 'A Podcast'").fetchone() == (0,)
    assert store.conn.execute("SELECT is_audiobook FROM books WHERE title = 'Apple'").fetchone() == (1,)


def test_reimport_replaces_child_rows():
    store = make_store()
    before = {t: store.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in CHILD_TABLES}
    store.replace_all([*load_ale_csv(SAMPLE), *EXTRA])
    after = {t: store.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in CHILD_TABLES}
    assert before == after  # nothing orphaned, nothing doubled
    assert assert_index_matches_blob(store) > 0


def test_replacing_with_fewer_books_leaves_no_orphans():
    store = make_store()
    store.replace_all(load_ale_csv(SAMPLE))
    for table in CHILD_TABLES:
        orphans = store.conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE book_id NOT IN (SELECT id FROM books)"
        ).fetchone()[0]
        assert orphans == 0
    assert assert_index_matches_blob(store) == 12


def test_fresh_database_is_at_current_version():
    assert make_store().conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


LEGACY_SCHEMA = """
CREATE TABLE books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    data TEXT NOT NULL
);
"""


def make_legacy_db(path: Path, books) -> None:
    """A database as written before the derived columns existed (user_version 0)."""
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    with conn:
        conn.executemany(
            "INSERT INTO books (source_id, title, data) VALUES (?, ?, ?)",
            [(b.source_id, b.title, b.model_dump_json()) for b in books],
        )
    conn.close()


def test_legacy_database_is_rebuilt_from_data_without_reimport(tmp_path):
    books = [*load_ale_csv(SAMPLE), *EXTRA]
    path = tmp_path / "old.db"
    make_legacy_db(path, books)
    before = sqlite3.connect(path).execute("SELECT id, data FROM books ORDER BY id").fetchall()

    store = SqliteLibraryStore(path)

    assert store.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert store.conn.execute("SELECT id, data FROM books ORDER BY id").fetchall() == before
    assert sorted(b.source_id for b in store.all_books()) == sorted(b.source_id for b in books)
    assert assert_index_matches_blob(store) == len(books)


def test_rebuild_is_atomic(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    make_legacy_db(path, load_ale_csv(SAMPLE))

    def boom(self, rows):
        raise RuntimeError("interrupted")

    monkeypatch.setattr(SqliteLibraryStore, "_insert", boom)
    with pytest.raises(RuntimeError):
        SqliteLibraryStore(path)
    monkeypatch.undo()

    # The failed rebuild left the old database untouched, and the next open succeeds.
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 12
    conn.close()
    assert len(SqliteLibraryStore(path).all_books()) == 12


def test_reopening_a_current_database_changes_nothing(tmp_path):
    path = tmp_path / "lib.db"
    make_store(path).conn.close()
    store = SqliteLibraryStore(path)
    assert len(store.all_books()) == len(EXTRA) + 12
    assert assert_index_matches_blob(store) > 0
