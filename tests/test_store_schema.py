"""The derived columns and child tables must always agree with the ``data`` blob."""

import sqlite3
from pathlib import Path

import pytest

from awkward import EXTRA
from l_space.ingestion.loaders import load_ale_csv
from l_space.library.engine import create_db_engine
from l_space.library.filters import BookFilter
from l_space.library.store import LibraryStore
from l_space.library.store_sql import (
    INDEX_COLUMNS,
    SCHEMA_VERSION,
    _to_book,
    index_rows,
    insert_books,
)
from l_space.library.store_sqlite import SqliteLibraryStore

SAMPLE = Path(__file__).parent.parent / "data" / "sample" / "library.csv"
CHILD_TABLES = ("book_people", "book_series", "book_tags")


def make_store(path=":memory:") -> SqliteLibraryStore:
    store = SqliteLibraryStore(path)
    store.replace_all([*load_ale_csv(SAMPLE), *EXTRA])
    return store


def sql(store: SqliteLibraryStore, query: str, params=()) -> list[tuple]:
    """Run raw SQL on the store's engine and return plain tuples."""
    with store.engine.connect() as conn:
        return [tuple(row) for row in conn.exec_driver_sql(query, params).all()]


def child_rows(store: SqliteLibraryStore, table: str, book_id: int) -> list:
    cols = {
        "book_people": "role, name, name_cf",
        "book_series": "name, name_cf, position, position_end",
        "book_tags": "tag_cf",
    }[table]
    rows = sql(store, f"SELECT {cols} FROM {table} WHERE book_id = ?", (book_id,))
    return sorted(rows, key=repr)


def assert_index_matches_blob(store: SqliteLibraryStore) -> int:
    """Every row's columns and child rows equal what ``index_rows`` derives from its blob."""
    checked = 0
    for id_, data in sql(store, "SELECT id, data FROM books"):
        ix = index_rows(_to_book(id_, data))
        row = sql(store, f"SELECT {', '.join(INDEX_COLUMNS)} FROM books WHERE id = ?", (id_,))
        assert row == [ix.values]
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
    names = sql(store, "SELECT name, name_cf FROM book_people WHERE name LIKE 'Łukasz%' LIMIT 1")
    assert names == [("Łukasz Żółć", "łukasz żółć")]
    assert sql(store, "SELECT 1 FROM book_series WHERE name_cf = 'strasse saga'")
    assert sql(store, "SELECT is_audiobook FROM books WHERE title = 'A Podcast'") == [(0,)]
    assert sql(store, "SELECT is_audiobook FROM books WHERE title = 'Apple'") == [(1,)]


def test_reimport_replaces_child_rows():
    store = make_store()

    def counts():
        return {t: sql(store, f"SELECT COUNT(*) FROM {t}")[0][0] for t in CHILD_TABLES}

    before = counts()
    store.replace_all([*load_ale_csv(SAMPLE), *EXTRA])
    assert counts() == before  # nothing orphaned, nothing doubled
    assert assert_index_matches_blob(store) > 0


def test_replacing_with_fewer_books_leaves_no_orphans():
    store = make_store()
    store.replace_all(load_ale_csv(SAMPLE))
    for table in CHILD_TABLES:
        orphans = sql(
            store, f"SELECT COUNT(*) FROM {table} WHERE book_id NOT IN (SELECT id FROM books)"
        )
        assert orphans == [(0,)]
    assert assert_index_matches_blob(store) == 12


def test_fresh_database_is_at_current_version():
    assert sql(make_store(), "PRAGMA user_version") == [(SCHEMA_VERSION,)]


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
    conn = sqlite3.connect(path)
    before = conn.execute("SELECT id, data FROM books ORDER BY id").fetchall()
    conn.close()

    store = SqliteLibraryStore(path)

    assert sql(store, "PRAGMA user_version") == [(SCHEMA_VERSION,)]
    assert sql(store, "SELECT id, data FROM books ORDER BY id") == before
    assert sorted(b.source_id for b in store.all_books()) == sorted(b.source_id for b in books)
    assert assert_index_matches_blob(store) == len(books)


def test_rebuild_is_atomic(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    make_legacy_db(path, load_ale_csv(SAMPLE))

    def boom(conn, rows):
        raise RuntimeError("interrupted")

    monkeypatch.setattr("l_space.library.store_sql.insert_books", boom)
    with pytest.raises(RuntimeError):
        SqliteLibraryStore(path)
    monkeypatch.undo()

    # The failed rebuild left the old database untouched (including its tables: the DDL was
    # rolled back too), and the next open succeeds.
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 12
    assert [r[1] for r in conn.execute("PRAGMA table_info(books)")] == [
        "id", "source_id", "title", "data",
    ]
    conn.close()
    assert len(SqliteLibraryStore(path).all_books()) == 12


def test_reopening_a_current_database_changes_nothing(tmp_path):
    path = tmp_path / "lib.db"
    make_store(path).engine.dispose()
    store = SqliteLibraryStore(path)
    assert len(store.all_books()) == len(EXTRA) + 12
    assert assert_index_matches_blob(store) > 0


# The version-2 schema exactly as the previous release created it (hand-written SQL). A database
# made by that release must open untouched under the metadata-driven store.
V2_DDL = (
    '\n    CREATE TABLE books (\n        id INTEGER PRIMARY KEY AUTOINCREMENT,\n        source_id TEXT NOT NULL UNIQUE,\n        title TEXT NOT NULL,\n        data TEXT NOT NULL,  -- the full Book as JSON, including the untouched source row\n        is_audiobook INTEGER NOT NULL,\n        format TEXT,\n        duration_minutes INTEGER,\n        my_rating REAL,\n        finished INTEGER,  -- 1 / 0 / NULL (unknown)\n        release_date TEXT,  -- ISO 8601\n        parent_category_cf TEXT NOT NULL,  -- \'\' when absent, like ``or ""`` in filter_books\n        child_category_cf TEXT NOT NULL\n    )\n    ',
    'CREATE INDEX idx_books_duration ON books (duration_minutes)',
    'CREATE INDEX idx_books_my_rating ON books (my_rating)',
    "\n    CREATE TABLE book_people (\n        book_id INTEGER NOT NULL REFERENCES books (id) ON DELETE CASCADE,\n        role TEXT NOT NULL CHECK (role IN ('author', 'narrator')),\n        name TEXT NOT NULL,\n        name_cf TEXT NOT NULL\n    )\n    ",
    'CREATE INDEX idx_book_people_book ON book_people (book_id, role)',
    '\n    CREATE TABLE book_series (\n        book_id INTEGER NOT NULL REFERENCES books (id) ON DELETE CASCADE,\n        name TEXT NOT NULL,\n        name_cf TEXT NOT NULL,\n        position REAL,\n        position_end REAL\n    )\n    ',
    'CREATE INDEX idx_book_series_book ON book_series (book_id)',
    '\n    CREATE TABLE book_tags (\n        book_id INTEGER NOT NULL REFERENCES books (id) ON DELETE CASCADE,\n        tag_cf TEXT NOT NULL\n    )\n    ',
    'CREATE INDEX idx_book_tags_book ON book_tags (book_id)',
)


def test_a_database_written_by_the_previous_release_opens_untouched(tmp_path):
    path = tmp_path / "v2.db"
    books = [*load_ale_csv(SAMPLE), *EXTRA]
    conn = sqlite3.connect(path)
    for statement in V2_DDL:
        conn.execute(statement)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    conn.close()
    engine = create_db_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        insert_books(conn, [(None, b) for b in books])
    engine.dispose()
    raw = sqlite3.connect(path)
    ddl_before = raw.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    raw.close()

    store = SqliteLibraryStore(path)

    raw = sqlite3.connect(path)
    assert raw.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall() == ddl_before
    raw.close()  # no rebuild: the hand-written tables are still the ones in place
    assert assert_index_matches_blob(store) == len(books)
    for kwargs in ({}, {"author": "verrow"}, {"category": "fantasy", "min_minutes": 100}):
        f = BookFilter(**kwargs)
        assert store.filter_books(f) == LibraryStore.filter_books(store, f)
        assert store.count_books(f) == len(LibraryStore.filter_books(store, f))
