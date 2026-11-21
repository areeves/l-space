from l_space.ask.executor import execute, format_minutes, render
from l_space.ask.plan import QueryPlan


def run(books, **fields):
    return execute(QueryPlan(**fields), books)


def test_format_minutes():
    assert format_minutes(860) == "14h 20m"
    assert format_minutes(45) == "45m"
    assert format_minutes(None) is None


def test_list_books_rows_and_sorting(books):
    result = run(books, op="list_books", sort="longest", limit=2)
    assert result.total == 11
    assert [r["title"] for r in result.rows] == ["The Saltmarch Omnibus", "Thornwood Accord"]
    assert result.truncated is True
    assert result.rows[0]["length"] == "41h 10m"
    assert result.rows[0]["series"] == "Saltmarch Collected #1-3"


def test_unknown_values_sort_last(books):
    result = run(books, op="list_books", sort="shortest", limit=50)
    assert result.rows[0]["title"] == "Between the Tides"
    assert result.rows[-1]["title"] == "The Hollow Lantern"  # no length on record


def test_count_books_has_no_rows(books):
    result = run(books, op="count_books", author="Maren Oakley")
    assert (result.total, result.rows) == (4, [])
    assert render(result) == "4 books match."


def test_narrator_counts_rows(books):
    result = run(books, op="narrator_counts", min_my_rating=5)
    assert result.total == 3
    assert {r["narrator"] for r in result.rows} == {"Corin Ashdale", "Petra Lindqvist", "Joss Harlan"}


def test_series_gaps_with_series_filter(books):
    assert run(books, op="series_gaps").rows == [
        {"series": "Ashfall Cycle", "owned_books": [1, 2, 4], "missing_books": [3]}
    ]
    assert run(books, op="series_gaps", series="tidewatch").total == 0


def test_in_progress_shows_time_left(books):
    result = run(books, op="in_progress")
    assert [r["title"] for r in result.rows] == ["Thornwood Betrayal"]
    assert result.rows[0]["time_left"] == "10h 48m"


def test_unsupported_and_empty_results(books):
    assert run(books, op="unsupported").total == 0
    empty = run(books, op="list_books", author="Nobody Here")
    assert render(empty) == "Nothing in your library matches that."


def test_render_marks_partial_lists(books):
    text = render(run(books, op="list_books", limit=2))
    assert text.splitlines()[0] == "Showing 2 of 11:"
    assert text.splitlines()[1].startswith("- ")
