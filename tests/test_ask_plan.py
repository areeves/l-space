import pytest
from pydantic import ValidationError

from l_space.ask.plan import QueryPlan, plan_schema


def test_minimal_plan_has_defaults():
    plan = QueryPlan.model_validate_json('{"op": "series_gaps"}')
    assert plan.sort == "title"
    assert plan.limit == 20
    assert plan.author is None


def test_blank_filters_become_none():
    plan = QueryPlan.model_validate_json(
        '{"op": "list_books", "author": "", "narrator": "null", "series": " None ",'
        ' "min_minutes": "", "min_my_rating": null}'
    )
    assert plan.author is None
    assert plan.narrator is None
    assert plan.series is None
    assert plan.min_minutes is None


def test_limit_is_clamped_not_rejected():
    assert QueryPlan(op="list_books", limit=0).limit == 1
    assert QueryPlan(op="list_books", limit=10_000).limit == 50
    assert QueryPlan(op="list_books", limit="lots").limit == 20


def test_unknown_operation_is_rejected():
    with pytest.raises(ValidationError):
        QueryPlan.model_validate_json('{"op": "drop_table"}')


def test_unknown_sort_is_rejected():
    with pytest.raises(ValidationError):
        QueryPlan.model_validate_json('{"op": "list_books", "sort": "randomly"}')


def test_invalid_json_is_a_validation_error():
    with pytest.raises(ValidationError):
        QueryPlan.model_validate_json("not json at all")


def test_schema_lists_every_operation():
    ops = plan_schema()["properties"]["op"]["enum"]
    assert "unsupported" in ops
    assert set(ops) >= {"list_books", "count_books", "series_gaps", "in_progress"}


def test_router_prompt_covers_the_failure_modes_seen_in_live_runs():
    from l_space.ask.router import SYSTEM_PROMPT

    # min/max direction, narrator vs author, in_progress, and what the library does not record
    for needle in ('max_minutes: "shorter than"', "WROTE", "READ", "in_progress", "NO prices, wishlists"):
        assert needle in SYSTEM_PROMPT
