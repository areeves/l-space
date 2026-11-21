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

    def generate(self, prompt, system=None, schema=None):
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
