"""The SQL filter path must agree exactly with the pure-Python reference, including order."""

from pathlib import Path

import pytest

from awkward import EXTRA
from l_space.ingestion.loaders import load_ale_csv
from l_space.library.filters import BookFilter
from l_space.library.store import LibraryStore
from l_space.library.store_sql import SqlLibraryStore

SAMPLE = Path(__file__).parent.parent / "data" / "sample" / "library.csv"

FILTERS = [
    {},
    {"author": "verrow"},
    {"author": "  VERROW "},
    {"author": " "},  # whitespace-only needle: truthy, strips to empty
    {"author": "łukasz żółć"},
    {"author": "ŁUKASZ"},
    {"author": "%"},
    {"author": "_eal"},
    {"narrator": "joss"},
    {"narrator": "émilie"},
    {"series": "saltmarch"},
    {"series": "STRASSE"},  # casefold: ß -> ss
    {"category": "fantasy"},
    {"category": "épique"},
    {"category": "zzz-no-such"},
    {"category": " "},  # whitespace-only: matches every audiobook, even with no category
    {"category": "epic"},  # child category and tags
    {"category": "grimdark"},
    {"series": " "},
    {"narrator": " "},
    {"author": "dup"},
    {"max_minutes": 1},
    {"min_my_rating": 3.5},
    {"min_minutes": 0},
    {"min_minutes": 900},
    {"max_minutes": 60},
    {"min_minutes": 100, "max_minutes": 700},
    {"min_my_rating": 0},
    {"min_my_rating": 5},
    {"author": "maren", "max_minutes": 700},
    {"narrator": "a", "min_my_rating": 4, "category": "fantasy"},
]


@pytest.fixture(scope="module")
def store(empty_store) -> SqlLibraryStore:
    empty_store.replace_all([*load_ale_csv(SAMPLE), *EXTRA])
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
