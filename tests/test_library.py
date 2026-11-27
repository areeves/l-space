from pathlib import Path

from l_space.ingestion.loaders import load_ale_csv
from l_space.library import queries
from l_space.library.store import LibraryStore
from stores import new_store

SAMPLE = Path(__file__).parent.parent / "data" / "sample" / "library.csv"


def make_store() -> LibraryStore:
    store = new_store(":memory:")
    store.replace_all(load_ale_csv(SAMPLE))
    return store


def by_title(store: LibraryStore) -> dict:
    return {b.title: b for b in store.all_books()}


def test_import_is_idempotent():
    store = make_store()
    assert store.replace_all(load_ale_csv(SAMPLE)) == 12
    assert len(store.all_books()) == 12


def test_fields_are_parsed_and_raw_row_preserved():
    book = by_title(make_store())["The Ember Crown"]
    assert book.source_id == "SYN0012"
    assert book.full_title == "The Ember Crown: Ashfall Cycle, Book 1"
    assert book.duration_minutes == 14 * 60 + 20
    assert book.finished is True
    assert book.my_rating == 5.0
    assert book.tags == ["Epic", "Fantasy", "War"]
    assert book.raw["Series"] == "Ashfall Cycle (book 1)"


def test_credit_roles_stripped_but_kept_in_raw():
    book = by_title(make_store())["Thornwood Accord"]
    assert book.authors == ["Dev Halloran", "Ana Pell"]
    assert "translator" in book.raw["Authors"]


def test_rows_with_missing_store_metadata_still_load():
    book = by_title(make_store())["The Hollow Lantern"]
    assert book.unavailable is True
    assert book.duration_minutes is None
    assert book.narrators == []


def test_in_progress():
    titles = [b.title for b in queries.in_progress(make_store().all_books())]
    assert titles == ["Thornwood Betrayal"]
    [book] = queries.in_progress(make_store().all_books())
    assert book.minutes_left == 10 * 60 + 48


def test_series_gaps():
    gaps = {g["series"]: g for g in queries.series_gaps(make_store().all_books())}
    assert gaps["Ashfall Cycle"]["missing"] == [3]
    # Tidewatch 1 and 2 are owned; 1.5 is ignored; the unnumbered umbrella series is skipped
    assert "Tidewatch" not in gaps
    assert "The Saltmarch Cycle" not in gaps
    # The omnibus covers 1-3, so no gaps are reported for it
    assert "Saltmarch Collected" not in gaps


def test_narrator_counts_exclude_podcasts():
    counts = dict(queries.narrator_counts(make_store().all_books()))
    assert counts["Corin Ashdale"] == 4
    assert counts["Petra Lindqvist"] == 4  # the podcast episode is not counted


def test_narrator_counts_filtered_by_my_rating():
    counts = dict(queries.narrator_counts(make_store().all_books(), min_my_rating=5))
    assert counts["Corin Ashdale"] == 2
    assert counts["Joss Harlan"] == 1  # co-narrator credit on a 5-star book


# The store's aggregate methods are the reference for SQL overrides (step 1-2): they must
# match the pure functions in queries.py, including the semantics the API relies on.
def awkward_store() -> LibraryStore:
    from awkward import EXTRA
    from l_space.library.models import Book, SeriesEntry

    podcast = Book(
        source_id="P1", title="Pod", format="Podcast", series=[SeriesEntry(name="Pod Run", position=3)]
    )
    store = make_store()
    store.replace_all([*load_ale_csv(SAMPLE), *EXTRA, podcast])
    return store


def test_aggregates_match_the_pure_functions():
    from l_space.library.filters import BookFilter

    store = awkward_store()
    books = store.all_books()
    assert store.narrator_counts() == queries.narrator_counts(books)
    assert store.narrator_counts(BookFilter(min_my_rating=4)) == queries.narrator_counts(books, 4)
    assert store.author_counts() == queries.author_counts(books)
    f = BookFilter(author="verrow")
    assert store.author_counts(f) == queries.author_counts(queries.filter_books(books, author="verrow"))
    assert store.in_progress() == queries.in_progress(books)


def test_series_gaps_cover_every_format():
    store = awkward_store()
    gaps = {g["series"]: g["missing"] for g in store.series_gaps()}
    assert gaps["Pod Run"] == [1, 2]  # a podcast still counts, unlike the audiobook filters
    assert store.series_gaps() == queries.series_gaps(store.all_books())
