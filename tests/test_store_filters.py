"""The SQL filter path must agree exactly with the pure-Python reference, including order."""

from pathlib import Path

import pytest

from awkward import EXTRA, FILTERS
from l_space.ingestion.loaders import load_ale_csv
from l_space.library.filters import BookFilter
from l_space.library.store import LibraryStore
from l_space.library.store_sql import SqlLibraryStore

SAMPLE = Path(__file__).parent.parent / "data" / "sample" / "library.csv"

@pytest.fixture(scope="module")
def store(empty_store) -> SqlLibraryStore:
    empty_store.import_books([*load_ale_csv(SAMPLE), *EXTRA])
    return empty_store


@pytest.mark.parametrize("kwargs", FILTERS, ids=[str(k) for k in FILTERS])
def test_sql_matches_reference(store, kwargs):
    f = BookFilter(**kwargs)
    # Call the base-class implementation explicitly: the pure-Python oracle.
    expected = LibraryStore.filter_books(store, f)
    got = store.filter_books(f)
    assert [b.id for b in got] == [b.id for b in expected]
    assert got == expected  # same hydrated books, including ids
    assert store.count_books(f) == LibraryStore.count_books(store, f) == len(expected)


def test_default_filter_excludes_only_non_books(store):
    titles = {b.title for b in store.filter_books()}
    assert "A Podcast" not in titles
    assert "Apple" in titles  # NULL format still counts as an audiobook
