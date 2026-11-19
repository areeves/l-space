from pathlib import Path

from l_space.ingestion.loaders import load_csv
from l_space.library import queries
from l_space.library.store import LibraryStore

SAMPLE = Path(__file__).parent.parent / "data" / "sample" / "library.csv"


def make_store() -> LibraryStore:
    store = LibraryStore(":memory:")
    store.replace_all(load_csv(SAMPLE))
    return store


def test_import_is_idempotent():
    store = make_store()
    assert store.replace_all(load_csv(SAMPLE)) == 10
    assert len(store.all_books()) == 10


def test_raw_row_preserved():
    books = {b.title: b for b in make_store().all_books()}
    assert books["The Ember Crown"].raw["series"] == "Ashfall Cycle, Book 1"
    assert books["The Ember Crown"].series == "Ashfall Cycle"


def test_stalled_series():
    result = {r["series"]: r for r in queries.stalled_series(make_store().all_books())}
    assert result["Ashfall Cycle"]["next_up"] == "The Ash Regent"
    assert result["Tidewatch"]["next_up"] == "The Drowned Archive"
    # Guild of Thorns book 2 is in progress, and Moonmarked has nothing finished
    assert "Guild of Thorns" not in result
    assert "Moonmarked" not in result


def test_narrator_counts_with_rating_filter():
    counts = dict(queries.narrator_counts(make_store().all_books(), min_rating=5))
    assert counts["Corin Ashdale"] == 3 - 1  # one Ashdale book is rated 4
    assert "Joss Harlan" in counts  # co-narrator credit on a 5-star book
