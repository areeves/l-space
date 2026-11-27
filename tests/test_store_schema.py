"""The derived columns and child tables must always agree with the ``data`` blob."""

from pathlib import Path

from awkward import EXTRA
from l_space.ingestion.loaders import load_ale_csv
from l_space.library.store_sql import INDEX_COLUMNS, SqlLibraryStore, _to_book, index_rows
from stores import new_store

SAMPLE = Path(__file__).parent.parent / "data" / "sample" / "library.csv"
CHILD_TABLES = ("book_people", "book_series", "book_tags")


def make_store(path=":memory:") -> SqlLibraryStore:
    store = new_store(path)
    store.import_books([*load_ale_csv(SAMPLE), *EXTRA])
    return store


def sql(store: SqlLibraryStore, query: str, params=()) -> list[tuple]:
    """Run raw SQL on the store's engine and return plain tuples."""
    with store.engine.connect() as conn:
        return [tuple(row) for row in conn.exec_driver_sql(query, params).all()]


def child_rows(store: SqlLibraryStore, table: str, book_id: int) -> list:
    cols = {
        "book_people": "role, name, name_cf",
        "book_series": "name, name_cf, position, position_end",
        "book_tags": "tag_cf",
    }[table]
    rows = sql(store, f"SELECT {cols} FROM {table} WHERE book_id = ?", (book_id,))
    return sorted(rows, key=repr)


def assert_index_matches_blob(store: SqlLibraryStore) -> int:
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
    store.import_books([*load_ale_csv(SAMPLE), *EXTRA])
    assert counts() == before  # nothing orphaned, nothing doubled
    assert assert_index_matches_blob(store) > 0


def test_replacing_with_fewer_books_leaves_no_orphans():
    store = make_store()
    store.import_books(load_ale_csv(SAMPLE))
    for table in CHILD_TABLES:
        orphans = sql(
            store, f"SELECT COUNT(*) FROM {table} WHERE book_id NOT IN (SELECT id FROM books)"
        )
        assert orphans == [(0,)]
    assert assert_index_matches_blob(store) == 12


def test_reopening_a_database_changes_nothing(tmp_path):
    path = tmp_path / "lib.db"
    make_store(path).engine.dispose()
    store = new_store(path)
    assert len(store.all_books()) == len(EXTRA) + 12
    assert assert_index_matches_blob(store) > 0
