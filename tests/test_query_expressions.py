"""The filter expressions compile for both supported dialects (decision D1)."""

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite

from l_space.library import tables
from l_space.library.filters import BookFilter
from l_space.library.store_sql import build_where, contains, title_order

DIALECTS = {"sqlite": sqlite.dialect(), "postgresql": postgresql.dialect()}
FUNCTION = {"sqlite": "instr(", "postgresql": "strpos("}
OTHER = {"sqlite": "strpos(", "postgresql": "instr("}


def sql(expr, name: str) -> str:
    return str(expr.compile(dialect=DIALECTS[name]))


@pytest.mark.parametrize("name", DIALECTS)
def test_substring_matching_uses_the_dialects_position_function(name):
    text = sql(contains(tables.book_people.c.name_cf, "x"), name)
    assert FUNCTION[name] in text and OTHER[name] not in text
    assert "LIKE" not in text


@pytest.mark.parametrize("name", DIALECTS)
def test_needles_are_bound_parameters_and_casefolded(name):
    compiled = contains(tables.book_people.c.name_cf, "  STRASSE ").compile(
        dialect=DIALECTS[name]
    )
    assert "STRASSE" not in str(compiled)
    assert list(compiled.params.values()) == ["strasse", 0]


@pytest.mark.parametrize("name", DIALECTS)
def test_every_filter_compiles(name):
    f = BookFilter(
        author="a", narrator="n", series="s", category="c",
        min_minutes=1, max_minutes=2, min_my_rating=3,
    )
    text = sql(select(tables.books.c.id).where(build_where(f)), name)
    assert text.count(FUNCTION[name]) == 6  # author, narrator, series, parent, child, tag
    assert OTHER[name] not in text


def test_postgres_orders_titles_by_code_point():
    assert 'COLLATE "C"' in sql(select(tables.books).order_by(title_order("postgresql")), "postgresql")
    assert "COLLATE" not in sql(select(tables.books).order_by(title_order("sqlite")), "sqlite")
