import importlib.util
import json
from pathlib import Path

from fakes import FakeProvider
from l_space.ask.plan import QueryPlan

RUN_PY = Path(__file__).parent.parent / "evals" / "run.py"
spec = importlib.util.spec_from_file_location("eval_run", RUN_PY)
eval_run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eval_run)


class OracleProvider(FakeProvider):
    """Plays a perfect router: answers each question with its expected plan."""

    def __init__(self, entries):
        super().__init__("{}")
        self.by_question = {e["question"]: json.dumps(e["expect"]) for e in entries}

    def generate(self, prompt, system=None, schema=None, max_tokens=None):
        if schema is not None:
            question = prompt.removeprefix("Q: ").split("\nA:")[0]
            return self.by_question[question]
        return "Done."


def test_dataset_is_valid_against_the_sample_library(books):
    entries = eval_run.load_questions(eval_run.DEFAULT_QUESTIONS)
    assert eval_run.validate_dataset(entries, books) == []
    kinds = {e["expect"]["op"] == "unsupported" for e in entries}
    assert kinds == {True, False}, "dataset needs both answerable and unanswerable questions"


def test_validate_catches_a_wrong_expected_total(books):
    entries = [{"id": "x", "question": "q", "expect": {"op": "count_books", "author": "Maren Oakley"}, "expect_total": 99}]
    assert "expect_total" in eval_run.validate_dataset(entries, books)[0]


def test_plan_matching_is_strict_about_extra_filters():
    expect = {"op": "list_books", "author": "Ilsa Verrow"}
    assert eval_run.plan_matches(expect, QueryPlan(op="list_books", author="ilsa verrow"))
    assert not eval_run.plan_matches(expect, QueryPlan(op="list_books", author="Ilsa Verrow", min_minutes=60))
    assert not eval_run.plan_matches(expect, QueryPlan(op="count_books", author="Ilsa Verrow"))
    assert eval_run.plan_matches({"op": "unsupported"}, QueryPlan(op="unsupported", reason="x"))


def test_result_match_forgives_equivalent_plans(books):
    entry = {"expect": {"op": "list_books", "author": "Ilsa Verrow"}, "expect_total": 3}
    other = QueryPlan(op="list_books", author="Verrow")  # different filter text, same books
    assert eval_run.result_matches(entry, other, books)
    assert not eval_run.result_matches(entry, QueryPlan(op="list_books", author="Oakley"), books)


def test_oracle_run_scores_perfectly_on_routing(books):
    entries = eval_run.load_questions(eval_run.DEFAULT_QUESTIONS)
    records = eval_run.evaluate(entries, books, OracleProvider(entries))
    summary = eval_run.summarize(records)
    assert summary["plan_match"] == 1.0
    assert summary["result_match"] == 1.0
    assert summary["abstain_recall"] == 1.0
    assert summary["false_abstain"] == 0.0
    # The oracle's one-word "Done." fails the grounding check, so fallbacks are counted
    assert summary["fallback"] is not None


def test_a_bad_router_is_scored_as_wrong(books):
    entries = eval_run.load_questions(eval_run.DEFAULT_QUESTIONS)[:1]  # the series-gaps question
    records = eval_run.evaluate(entries, books, FakeProvider('{"op": "unsupported"}'))
    summary = eval_run.summarize(records)
    assert summary["result_match"] == 0.0
    assert summary["false_abstain"] == 1.0


def test_scoring_catches_the_wrong_plans_from_the_first_live_run(books):
    """Plans llama3.2 actually produced on the first live run (see PR #2 discussion)."""
    entries = {e["id"]: e for e in eval_run.load_questions(eval_run.DEFAULT_QUESTIONS)}
    live_plans = {
        "list-narrator": {"op": "narrator_counts", "author": "Joss Harlan"},
        "in-progress": {"op": "list_books", "min_minutes": 0, "max_minutes": 1200, "sort": "newest"},
        "short-books": {"op": "list_books", "min_minutes": 60},
        "wishlist": None,  # covered by the router guard, see test_ask_pipeline
        # 3 five-star books and 3 five-star narrators: only the rows reveal the wrong operation
        "narrators-five-star": {"op": "list_books", "min_my_rating": 5.0},
    }
    for id_, plan in live_plans.items():
        if plan is None:
            continue
        entry = entries["unanswerable-wishlist" if id_ == "wishlist" else id_]
        assert not eval_run.result_matches(entry, QueryPlan(**plan), books), id_


def test_correct_plans_from_the_first_live_run_still_pass(books):
    entries = {e["id"]: e for e in eval_run.load_questions(eval_run.DEFAULT_QUESTIONS)}
    good = QueryPlan(op="narrator_counts", min_my_rating=5.0)
    assert eval_run.result_matches(entries["narrators-five-star"], good, books)
    # A limit on a superlative is fine: only the total and the top row are compared
    longest = QueryPlan(op="list_books", sort="longest", limit=1)
    assert eval_run.result_matches(entries["longest"], longest, books)


def test_heldout_set_is_valid_and_never_used_for_tuning(books):
    """The held-out questions measure generalization, so they must not leak into the prompt
    examples or the tuning set."""
    from l_space.ask.router import SYSTEM_PROMPT

    heldout = eval_run.load_questions(eval_run.DEFAULT_QUESTIONS.with_name("heldout.jsonl"))
    tuning = eval_run.load_questions(eval_run.DEFAULT_QUESTIONS)
    assert eval_run.validate_dataset(heldout, books) == []
    questions = {e["question"] for e in heldout}
    assert not questions & {e["question"] for e in tuning}
    assert not {e["id"] for e in heldout} & {e["id"] for e in tuning}
    assert not any(q in SYSTEM_PROMPT for q in questions)
    assert {e["expect"]["op"] == "unsupported" for e in heldout} == {True, False}


def test_evaluate_uses_the_router_for_plans_and_the_writer_for_text(books):
    entries = eval_run.load_questions(eval_run.DEFAULT_QUESTIONS)
    router = OracleProvider(entries)
    writer = FakeProvider("{}", "Done.")
    records = eval_run.evaluate(entries, books, writer, router)
    assert eval_run.summarize(records)["plan_match"] == 1.0
    assert writer.plan_calls == []  # the writer model was never asked to route


def _record(latency, **overrides):
    base = {
        "unanswerable": False, "plan_match": True, "result_match": True, "abstained": False,
        "grounded": True, "used_fallback": False, "router_error": None,
        "latency_ms": latency, "route_ms": latency / 4, "write_ms": latency / 2,
    }
    return {**base, **overrides}


def test_p95_is_nearest_rank_not_the_maximum():
    # 20 samples: the 95th percentile is the 19th value. A cold-start outlier must not become "p95".
    records = [_record(float(n)) for n in range(1, 20)] + [_record(127_000.0)]
    summary = eval_run.summarize(records)
    assert summary["latency_ms_p95"] == 19.0
    assert summary["latency_ms_max"] == 127_000.0
    assert summary["latency_ms_p50"] == 10.5


def test_percentiles_with_a_single_sample():
    summary = eval_run.summarize([_record(5.0)])
    assert summary["latency_ms_p95"] == summary["latency_ms_max"] == 5.0


def test_warm_up_loads_every_model_with_a_minimal_request():
    first, second = FakeProvider("{}"), FakeProvider("{}")
    eval_run.warm_up(first, second)
    assert len(first.write_calls) == len(second.write_calls) == 1
    assert first.max_tokens == second.max_tokens == [1]
