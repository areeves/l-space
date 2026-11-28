"""Queries must start from the user's own rows and use the schema's indexes.

The plans are read from the statements the library really runs (not rebuilt by hand), with
another user's books in the same database. A SCAN would read every user's rows.
"""

import pytest
from sqlalchemy import event

from awkward import EXTRA
from conftest import SAMPLE
from l_space.ingestion.loaders import load_ale_csv
from l_space.library.filters import BookFilter


@pytest.fixture(scope="module")
def library(empty_library):
    empty_library.import_books([*load_ale_csv(SAMPLE), *EXTRA])
    return empty_library


def plan(library, call) -> list[str]:
    """EXPLAIN QUERY PLAN for every SELECT that ``call(library)`` runs."""
    seen = []

    def grab(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            seen.append((statement, parameters))

    event.listen(library.engine, "before_cursor_execute", grab)
    try:
        call(library)
    finally:
        event.remove(library.engine, "before_cursor_execute", grab)
    with library.engine.connect() as conn:
        return [
            row[3]
            for statement, parameters in seen
            for row in conn.exec_driver_sql(f"EXPLAIN QUERY PLAN {statement}", parameters)
        ]


def listing(**filters):
    return lambda library: library.filter_books(BookFilter(**filters))


CALLS = {
    "all books": lambda library: library.all_books(),
    "author": listing(author="a"),
    "narrator": listing(narrator="a"),
    "series": listing(series="a"),
    "category": listing(category="a"),
    "min minutes": listing(min_minutes=100),
    "max minutes": listing(max_minutes=100),
    "min rating": listing(min_my_rating=4),
    "count": lambda library: library.count_books(BookFilter(author="a")),
    "in progress": lambda library: library.in_progress(),
    "narrator counts": lambda library: library.narrator_counts(),
    "narrator counts, rated": lambda library: library.narrator_counts(BookFilter(min_my_rating=4)),
    "author counts": lambda library: library.author_counts(),
    "series gaps": lambda library: library.series_gaps(),
}


@pytest.fixture(scope="module", autouse=True)
def sqlite_only(library):
    if library.engine.dialect.name != "sqlite":
        pytest.skip("plan text is SQLite's")


@pytest.mark.parametrize("name", CALLS)
def test_no_query_scans_a_table(library, name):
    steps = plan(library, CALLS[name])
    assert steps
    assert not [s for s in steps if s.startswith("SCAN")], steps


@pytest.mark.parametrize(
    ("name", "index"),
    [
        ("all books", "idx_books_user_title (user_id=?)"),
        ("author", "idx_book_people_book (book_id=? AND role=?)"),
        ("narrator", "idx_book_people_book (book_id=? AND role=?)"),
        ("series", "idx_book_series_book (book_id=?)"),
        ("category", "idx_book_tags_book (book_id=?)"),
        ("min minutes", "idx_books_user_duration (user_id=? AND duration_minutes>?)"),
        ("max minutes", "idx_books_user_duration (user_id=? AND duration_minutes<?)"),
        ("min rating", "idx_books_user_my_rating (user_id=? AND my_rating>?)"),
        ("narrator counts, rated", "idx_books_user_my_rating (user_id=? AND my_rating>?)"),
        ("narrator counts", "idx_book_people_book (book_id=? AND role=?)"),
    ],
)
def test_queries_use_their_indexes(library, name, index):
    assert any(index in step for step in plan(library, CALLS[name]))


@pytest.mark.parametrize("name", ["all books", "author", "narrator", "series", "category"])
def test_title_order_needs_no_sort(library, name):
    steps = plan(library, CALLS[name])
    assert not [s for s in steps if "TEMP B-TREE FOR ORDER BY" in s], steps
