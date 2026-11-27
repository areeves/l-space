from l_space.ask.executor import execute, format_minutes, render
from l_space.ask.plan import QueryPlan
from l_space.library import queries


def run(library, **fields):
    return execute(QueryPlan(**fields), library)


def test_format_minutes():
    assert format_minutes(860) == "14h 20m"
    assert format_minutes(45) == "45m"
    assert format_minutes(None) is None


def test_list_books_rows_and_sorting(library):
    result = run(library, op="list_books", sort="longest", limit=2)
    assert result.total == 11
    assert [r["title"] for r in result.rows] == ["The Saltmarch Omnibus", "Thornwood Accord"]
    assert result.truncated is True
    assert result.rows[0]["length"] == "41h 10m"
    assert result.rows[0]["series"] == "Saltmarch Collected #1-3"


def test_unknown_values_sort_last(library):
    result = run(library, op="list_books", sort="shortest", limit=50)
    assert result.rows[0]["title"] == "Between the Tides"
    assert result.rows[-1]["title"] == "The Hollow Lantern"  # no length on record


def test_count_books_has_no_rows(library):
    result = run(library, op="count_books", author="Maren Oakley")
    assert (result.total, result.rows) == (4, [])
    assert render(result) == "4 books match."


def test_narrator_counts_rows(library):
    result = run(library, op="narrator_counts", min_my_rating=5)
    assert result.total == 3
    assert {r["narrator"] for r in result.rows} == {"Corin Ashdale", "Petra Lindqvist", "Joss Harlan"}


def test_series_gaps_with_series_filter(library):
    assert run(library, op="series_gaps").rows == [
        {"series": "Ashfall Cycle", "owned_books": [1, 2, 4], "missing_books": [3]}
    ]
    assert run(library, op="series_gaps", series="tidewatch").total == 0


def test_in_progress_shows_time_left(library):
    result = run(library, op="in_progress")
    assert [r["title"] for r in result.rows] == ["Thornwood Betrayal"]
    assert result.rows[0]["time_left"] == "10h 48m"


def test_unsupported_and_empty_results(library):
    assert run(library, op="unsupported").total == 0
    empty = run(library, op="list_books", author="Nobody Here")
    assert render(empty) == "Nothing in your library matches that."


def test_render_marks_partial_lists(library):
    text = render(run(library, op="list_books", limit=2))
    assert text.splitlines()[0] == "Showing 2 of 11:"
    assert text.splitlines()[1].startswith("- ")


def test_narrator_and_author_counts_respect_every_filter(library, books):
    # Compared with the pure functions over the filtered books, not just with each other.
    only_verrow = run(library, op="narrator_counts", author="Ilsa Verrow", limit=50)
    expected = queries.narrator_counts(queries.filter_books(books, author="Ilsa Verrow"))
    assert [(r["narrator"], r["books"]) for r in only_verrow.rows] == expected
    assert only_verrow.rows != run(library, op="narrator_counts", limit=50).rows

    by_joss = run(library, op="author_counts", narrator="Joss Harlan", limit=50)
    expected = queries.author_counts(queries.filter_books(books, narrator="Joss Harlan"))
    assert [(r["author"], r["books"]) for r in by_joss.rows] == expected
    assert by_joss.rows != run(library, op="author_counts", limit=50).rows
