"""Filters must keep using the indexes the schema defines (not a full scan per child row)."""

import pytest
from sqlalchemy import select

from awkward import EXTRA
from conftest import SAMPLE
from l_space.ingestion.loaders import load_ale_csv
from l_space.library import tables
from l_space.library.filters import BookFilter
from l_space.library.store_sql import build_where, title_order


@pytest.fixture(scope="module")
def store(empty_store):
    empty_store.import_books([*load_ale_csv(SAMPLE), *EXTRA])
    return empty_store


def plan(store, **filters) -> list[str]:
    query = (
        select(tables.books.c.id, tables.books.c.data)
        .where(build_where(BookFilter(**filters)))
        .order_by(title_order(store.engine.dialect.name))
    )
    sql = str(query.compile(store.engine, compile_kwargs={"literal_binds": True}))
    with store.engine.connect() as conn:
        return [row[3] for row in conn.exec_driver_sql(f"EXPLAIN QUERY PLAN {sql}")]


@pytest.mark.parametrize(
    ("filters", "index"),
    [
        ({"author": "a"}, "idx_book_people_book (book_id=? AND role=?)"),
        ({"narrator": "a"}, "idx_book_people_book (book_id=? AND role=?)"),
        ({"series": "a"}, "idx_book_series_book (book_id=?)"),
        ({"category": "a"}, "idx_book_tags_book (book_id=?)"),
        ({"min_minutes": 100}, "idx_books_duration (duration_minutes>?)"),
        ({"max_minutes": 100}, "idx_books_duration (duration_minutes<?)"),
        ({"min_my_rating": 4}, "idx_books_my_rating (my_rating>?)"),
    ],
    ids=lambda v: str(v) if isinstance(v, dict) else "",
)
def test_filters_use_their_indexes(store, filters, index):
    if store.engine.dialect.name != "sqlite":
        pytest.skip("plan text is SQLite's")
    assert any(index in step for step in plan(store, **filters))
