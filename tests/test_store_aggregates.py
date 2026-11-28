"""SQL aggregates must agree exactly with the pure-Python functions in queries.py."""

from pathlib import Path

import pytest

from awkward import EXTRA, FILTERS
from l_space.ingestion.loaders import load_ale_csv
from l_space.library import queries
from l_space.library.filters import BookFilter
from l_space.library.models import Book, SeriesEntry

SAMPLE = Path(__file__).parent.parent / "data" / "sample" / "library.csv"

# Rows aimed at the aggregates: a narrator listed twice on one book, names that tie on count
# (and differ in case, so code-point order matters), started-but-unfinished books, a podcast in
# a series, an omnibus range, a fractional position and a series with no number at all.
AGGREGATE_ROWS = [
    Book(source_id="A1", title="Twice Told", authors=["Dup"], narrators=["Same", "Same"],
         finished=False, series=[SeriesEntry(name="Omni", position=1, position_end=3),
                                 SeriesEntry(name="Omni", position=5),
                                 SeriesEntry(name="Frac", position=22.5),
                                 SeriesEntry(name="Umbrella")]),
    Book(source_id="A2", title="Tie Breaker", authors=["Dup"], narrators=["b name", "Zed"],
         finished=False, my_rating=5.0),
    Book(source_id="A4", title="Shouting", narrators=["ZED"]),  # same name, different case
    Book(source_id="A3", title="Pod", format="Podcast", narrators=["Podcaster"], finished=False,
         series=[SeriesEntry(name="Pod Run", position=3)]),
]


@pytest.fixture(scope="module")
def store(empty_store):
    empty_store.import_books([*load_ale_csv(SAMPLE), *EXTRA, *AGGREGATE_ROWS])
    return empty_store


def scoped(store, f: BookFilter) -> list[Book]:
    """The reference: filter in Python, over every book."""
    return queries.filter_books(store.all_books(), **f.as_kwargs())


@pytest.mark.parametrize("kwargs", FILTERS, ids=[str(k) for k in FILTERS])
def test_counts_and_in_progress_match_the_reference(store, kwargs):
    f = BookFilter(**kwargs)
    books = scoped(store, f)
    assert store.narrator_counts(f) == queries.narrator_counts(books)
    assert store.author_counts(f) == queries.author_counts(books)
    assert store.in_progress(f) == queries.in_progress(books)


def test_without_a_filter(store):
    books = scoped(store, BookFilter())
    assert store.narrator_counts() == queries.narrator_counts(books)
    assert store.author_counts() == queries.author_counts(books)
    assert store.in_progress() == queries.in_progress(books)


def test_the_awkward_cases_are_actually_exercised(store):
    counts = dict(store.narrator_counts())
    assert counts["Same"] == 2  # listed twice on one book
    names = [n for n, c in store.narrator_counts() if c == counts["Zed"]]
    assert names.index("Zed") < names.index("b name")  # code-point order, not case-insensitive
    assert counts["ZED"] == 1 and counts["Zed"] == 1  # grouped exactly as written
    assert "Podcaster" not in counts  # not an audiobook
    assert {b.source_id for b in store.in_progress()} >= {"A1", "A2"}


def test_series_gaps_match_the_reference(store):
    assert store.series_gaps() == queries.series_gaps(store.all_books())
    gaps = {g["series"]: g for g in store.series_gaps()}
    assert gaps["Omni"]["missing"] == [4]  # 1-3 omnibus covers 1, 2 and 3
    assert gaps["Pod Run"]["missing"] == [1, 2]  # every format counts
    assert "Frac" not in gaps and "Umbrella" not in gaps
