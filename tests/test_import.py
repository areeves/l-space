"""Import is an upsert: rows keep their identity, unchanged rows are not touched (decision D7)."""

import random
from pathlib import Path

import pytest

from awkward import EXTRA
from l_space.ingestion.loaders import load_ale_csv
from l_space.library import store_sql
from l_space.library.models import Book, SeriesEntry
from l_space.library.store import ImportResult
from stores import new_library
from test_store_schema import assert_index_matches_blob, sql

SAMPLE = Path(__file__).parent.parent / "data" / "sample" / "library.csv"
BOOKS = [*load_ale_csv(SAMPLE), *EXTRA]


def ids(store) -> dict[str, int]:
    return dict(sql(store, "SELECT source_id, id FROM books"))


def changes(store) -> int:
    """Rows the database has modified so far (SQLite's own counter)."""
    with store.engine.connect() as conn:
        return conn.connection.dbapi_connection.total_changes


def edited(book: Book, **fields) -> Book:
    return book.model_copy(update=fields)


@pytest.fixture
def store():
    store = new_library()
    store.import_books(BOOKS)
    return store


def test_the_first_import_adds_everything():
    store = new_library()
    result = store.import_books(BOOKS)
    assert result == ImportResult(added=len(BOOKS), updated=0, unchanged=0, removed=0)
    assert result.total == len(store.all_books())


def test_importing_the_same_books_again_changes_nothing(store):
    before_ids = ids(store)
    before_rows = sql(store, "SELECT * FROM books ORDER BY id")
    before_changes = changes(store)

    result = store.import_books(reversed(BOOKS))  # order does not matter

    assert result == ImportResult(unchanged=len(BOOKS))
    assert ids(store) == before_ids
    assert sql(store, "SELECT * FROM books ORDER BY id") == before_rows
    assert changes(store) == before_changes  # nothing was written, not even rewritten


def test_a_changed_book_is_updated_in_place(store):
    target = BOOKS[0]
    old_id = ids(store)[target.source_id]

    new = edited(
        target,
        title="A New Title",
        authors=["Someone Else"],
        tags=["Fresh"],
        series=[SeriesEntry(name="Brand New", position=2)],
        my_rating=1.5,
    )
    result = store.import_books([new, *BOOKS[1:]])

    assert result == ImportResult(updated=1, unchanged=len(BOOKS) - 1)
    assert ids(store)[target.source_id] == old_id  # same row, same id
    stored = next(b for b in store.all_books() if b.source_id == target.source_id)
    assert (stored.title, stored.authors, stored.my_rating) == ("A New Title", ["Someone Else"], 1.5)
    # Derived columns and child rows follow the new data; no stale rows from the old one.
    assert assert_index_matches_blob(store) == len(BOOKS)
    assert sql(store, "SELECT name FROM book_series WHERE book_id = ?", (old_id,)) == [("Brand New",)]


def test_new_books_are_added_and_existing_ids_are_stable(store):
    before = ids(store)
    extra = Book(source_id="NEW1", title="Brand New Book", authors=["New Author"])
    result = store.import_books([*BOOKS, extra])
    assert result == ImportResult(added=1, unchanged=len(BOOKS))
    after = ids(store)
    assert {k: after[k] for k in before} == before
    assert after["NEW1"] not in before.values()


def test_books_missing_from_the_import_are_removed_with_their_rows(store):
    keep = BOOKS[:5]
    result = store.import_books(keep)
    assert result == ImportResult(unchanged=5, removed=len(BOOKS) - 5)
    assert {b.source_id for b in store.all_books()} == {b.source_id for b in keep}
    assert assert_index_matches_blob(store) == 5
    for table in ("book_people", "book_series", "book_tags"):
        orphans = sql(store, f"SELECT COUNT(*) FROM {table} WHERE book_id NOT IN (SELECT id FROM books)")
        assert orphans == [(0,)]


def test_one_import_can_add_update_and_remove(store):
    result = store.import_books(
        [edited(BOOKS[0], title="Retitled"), BOOKS[1], Book(source_id="NEW2", title="Newcomer")]
    )
    assert result == ImportResult(added=1, updated=1, unchanged=1, removed=len(BOOKS) - 2)


def test_duplicate_source_ids_in_one_import_count_once_and_the_last_wins():
    store = new_library()
    first, last = Book(source_id="D", title="First"), Book(source_id="D", title="Last")
    result = store.import_books([first, last])
    assert result == ImportResult(added=1)
    assert [b.title for b in store.all_books()] == ["Last"]


def test_an_empty_import_empties_the_library(store):
    assert store.import_books([]) == ImportResult(removed=len(BOOKS))
    assert store.all_books() == []


def test_a_failed_import_changes_nothing(store, monkeypatch):
    before = sql(store, "SELECT * FROM books ORDER BY id")

    def boom(*args, **kwargs):
        raise RuntimeError("interrupted")

    monkeypatch.setattr(store_sql, "insert_books", boom)
    changed = [edited(BOOKS[0], title="Retitled"), Book(source_id="NEW3", title="Newcomer")]
    with pytest.raises(RuntimeError):
        store.import_books(changed)  # the update ran, then the insert failed

    assert sql(store, "SELECT * FROM books ORDER BY id") == before
    assert assert_index_matches_blob(store) == len(BOOKS)


def test_any_sequence_of_imports_ends_where_a_fresh_import_would():
    # Random edits, additions and removals; after every step the store must equal a store that
    # imported the same books from scratch (ids aside), and its derived rows must agree.
    rng = random.Random(7)
    store = new_library()
    current: list[Book] = []
    pool = [*BOOKS, *(Book(source_id=f"R{i}", title=f"Random {i}") for i in range(10))]
    for step in range(25):
        current = [
            edited(b, title=b.title + "!", tags=[*b.tags, "t"]) if rng.random() < 0.3 else b
            for b in rng.sample(pool, rng.randint(0, len(pool)))
        ]
        store.import_books(current)
        fresh = new_library()
        fresh.import_books(current)
        strip = lambda books: [b.model_copy(update={"id": None, "public_id": None}) for b in books]
        assert strip(store.all_books()) == strip(fresh.all_books()), f"step {step}"
        assert assert_index_matches_blob(store) == len(current)


def test_books_belong_to_the_owner_and_get_opaque_public_ids(store):
    rows = sql(store, "SELECT user_id, public_id FROM books")
    owner = sql(store, "SELECT id FROM users")[0][0]
    assert {user_id for user_id, _ in rows} == {owner}
    public_ids = [public_id for _, public_id in rows]
    assert len(set(public_ids)) == len(BOOKS)
    assert all(len(p) >= 16 and p.isascii() and "/" not in p and " " not in p for p in public_ids)


def test_public_ids_survive_re_imports_and_updates(store):
    before = dict(sql(store, "SELECT source_id, public_id FROM books"))
    store.import_books(BOOKS)  # unchanged
    store.import_books([edited(BOOKS[0], title="Retitled"), *BOOKS[1:]])  # updated
    store.import_books([BOOKS[0], *BOOKS[2:]])  # one removed
    store.import_books(BOOKS)  # and back: the removed one is new again
    after = dict(sql(store, "SELECT source_id, public_id FROM books"))
    gone = BOOKS[1].source_id
    assert {k: v for k, v in after.items() if k != gone} == {k: v for k, v in before.items() if k != gone}
    assert after[gone] != before[gone]  # a re-added book is a new row with a new identifier
