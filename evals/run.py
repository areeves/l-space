"""Evaluate the question-answering pipeline against a fixed question set.

    python evals/run.py                  # needs Ollama running with the configured model
    python evals/run.py --validate       # no model: check the dataset against the sample library

Kept outside ``src/`` on purpose so prompt, schema, or model changes are always scored against
the same questions. Each answerable question carries the plan we expect (``expect``) and the
number of matches that plan must produce on the synthetic sample library (``expect_total``).

Metrics:
  plan match     the model's plan equals the expected one (op, and every filter listed;
                 unlisted filters must be empty)
  result match   running the model's plan returns the same total and rows as the expected plan
                 (or the same total and top row for "longest"-style questions), which forgives
                 different-but-equivalent plans but not a wrong operation
  abstention     unanswerable questions should become ``unsupported``; answerable ones must not
  grounded       share of model-written answers that passed the grounding check
  fallback       share of answers where the model's wording was replaced by deterministic text
  latency        p50, p95 (nearest rank) and max per question, after an untimed warm-up that
                 loads the models; route_ms / write_ms split the two model calls
"""

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path

import httpx2

from l_space.ask.answer import answer_question
from l_space.ask.executor import execute
from l_space.ask.plan import QueryPlan
from l_space.core.settings import get_settings
from l_space.ingestion.loaders import load_ale_csv
from l_space.library.store import LibraryStore
from l_space.library.store_sql import SqlLibraryStore
from l_space.models.provider import OllamaProvider

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUESTIONS = Path(__file__).with_name("questions.jsonl")
DEFAULT_LIBRARY = ROOT / "data" / "sample" / "library.csv"

FILTERS = ("author", "narrator", "series", "category", "min_minutes", "max_minutes", "min_my_rating")


def load_questions(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _norm(value):
    return value.casefold().strip() if isinstance(value, str) else value


def plan_matches(expect: dict, plan: QueryPlan) -> bool:
    if plan.op != expect["op"]:
        return False
    if plan.op == "unsupported":
        return True
    actual = plan.model_dump()
    for key in FILTERS:
        if _norm(actual[key]) != _norm(expect.get(key)):
            return False
    return all(actual[key] == expect[key] for key in ("sort", "limit") if key in expect)


def _row_set(rows: list[dict]) -> list[str]:
    return sorted(json.dumps(r, sort_keys=True) for r in rows)


def result_matches(entry: dict, plan: QueryPlan, store: LibraryStore) -> bool:
    """Does the model's plan give the user the same answer as the expected plan?

    Compares what actually comes back (the total and the rows), not just how many, so a wrong
    operation can't pass by coincidence (3 five-star books vs 3 five-star narrators). Equivalent
    plans still pass: "Verrow" and "Ilsa Verrow" return the same books. Questions with
    ``expect_first_title`` (superlatives like "longest") compare only the total and top row,
    since a model may reasonably add a limit.
    """
    if entry["expect"]["op"] == "unsupported":
        return plan.op == "unsupported"
    if plan.op == "unsupported":
        return False
    expected = execute(QueryPlan(**entry["expect"]), store)
    actual = execute(plan, store)
    if actual.total != expected.total:
        return False
    first = entry.get("expect_first_title")
    if first is not None:
        return bool(actual.rows) and actual.rows[0].get("title") == first
    return _row_set(actual.rows) == _row_set(expected.rows)


def validate_dataset(entries: list[dict], store: LibraryStore) -> list[str]:
    """Check that each expected plan is valid and produces its expected total. No model needed."""
    problems = []
    ids = [e["id"] for e in entries]
    if len(ids) != len(set(ids)):
        problems.append("duplicate question ids")
    for e in entries:
        try:
            plan = QueryPlan(**e["expect"])
        except Exception as exc:  # noqa: BLE001 - report any malformed entry
            problems.append(f"{e['id']}: invalid expected plan ({exc})")
            continue
        if plan.op == "unsupported":
            continue
        if "expect_total" not in e:
            problems.append(f"{e['id']}: answerable question without expect_total")
        elif execute(plan, store).total != e["expect_total"]:
            problems.append(f"{e['id']}: expected plan does not produce expect_total on the sample")
        elif "expect_first_title" in e and not result_matches(e, plan, store):
            problems.append(f"{e['id']}: expected plan does not put expect_first_title first")
    return problems


def warm_up(*providers) -> None:
    """Load each model before timing starts, so a cold start (which can take minutes for a
    7B model on an older CPU) is not counted as the latency of the first question."""
    for provider in providers:
        provider.generate("Hello", max_tokens=1)


def evaluate(entries: list[dict], store: LibraryStore, provider, router=None) -> list[dict]:
    records = []
    for e in entries:
        started = time.perf_counter()
        answer = answer_question(e["question"], store, provider, router)
        elapsed = (time.perf_counter() - started) * 1000
        unanswerable = e["expect"]["op"] == "unsupported"
        records.append(
            {
                "id": e["id"],
                "question": e["question"],
                "unanswerable": unanswerable,
                "plan": answer.plan.model_dump(exclude_none=True),
                "plan_match": plan_matches(e["expect"], answer.plan),
                "result_match": result_matches(e, answer.plan, store),
                "abstained": answer.abstained,
                "grounded": answer.grounded,
                "used_fallback": answer.used_fallback,
                "ungrounded_terms": answer.ungrounded_terms,
                "router_error": answer.router_error,
                "answer": answer.text,
                "latency_ms": round(elapsed, 1),
                "route_ms": answer.timings_ms.get("route"),
                "write_ms": answer.timings_ms.get("write"),  # None when no draft was written
            }
        )
    return records


def _rate(hits: int, total: int) -> float | None:
    return round(hits / total, 3) if total else None


def summarize(records: list[dict]) -> dict:
    answerable = [r for r in records if not r["unanswerable"]]
    unanswerable = [r for r in records if r["unanswerable"]]
    drafts = [r for r in records if r["grounded"] is not None]
    latencies = sorted(r["latency_ms"] for r in records)
    # nearest-rank percentile: the smallest value with at least 95% of samples at or below it
    p95 = latencies[math.ceil(0.95 * len(latencies)) - 1] if latencies else None
    route_times = [r["route_ms"] for r in records if r.get("route_ms") is not None]
    write_times = [r["write_ms"] for r in records if r.get("write_ms") is not None]
    return {
        "questions": len(records),
        "answerable": len(answerable),
        "unanswerable": len(unanswerable),
        "plan_match": _rate(sum(r["plan_match"] for r in answerable), len(answerable)),
        "result_match": _rate(sum(r["result_match"] for r in answerable), len(answerable)),
        "abstain_recall": _rate(sum(r["abstained"] for r in unanswerable), len(unanswerable)),
        "false_abstain": _rate(sum(r["abstained"] for r in answerable), len(answerable)),
        "writer_drafts": len(drafts),
        "grounded": _rate(sum(bool(r["grounded"]) for r in drafts), len(drafts)),
        "fallback": _rate(sum(r["used_fallback"] for r in records), len(records)),
        "router_errors": sum(bool(r["router_error"]) for r in records),
        "latency_ms_p50": round(statistics.median(latencies), 1) if latencies else None,
        "latency_ms_p95": p95,
        "latency_ms_max": latencies[-1] if latencies else None,
        "route_ms_p50": round(statistics.median(route_times), 1) if route_times else None,
        "write_ms_p50": round(statistics.median(write_times), 1) if write_times else None,
    }


def _print_report(summary: dict, records: list[dict], model: str, router_model: str) -> None:
    print(f"\nModel: {model}" + (f"  (router: {router_model})" if router_model != model else ""))
    for key, value in summary.items():
        print(f"  {key:<16} {value}")
    failures = [r for r in records if not r["result_match"] or r["used_fallback"] or r["router_error"]]
    if failures:
        print("\nWorth a look:")
        for r in failures:
            flags = []
            if not r["plan_match"]:
                flags.append("plan differs")
            if not r["result_match"]:
                flags.append("WRONG RESULT")
            if r["used_fallback"]:
                flags.append(f"fallback {r['ungrounded_terms'] or ''}".strip())
            if r["router_error"]:
                flags.append(f"router error: {r['router_error']}")
            print(f"  [{r['id']}] {r['question']}\n      plan={r['plan']}  ({'; '.join(flags)})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    parser.add_argument("--model", help="Ollama model that writes answers (default: from settings)")
    parser.add_argument(
        "--router-model", help="Ollama model that routes questions (default: same as --model)"
    )
    parser.add_argument("--url", help="Ollama base URL (default: from settings)")
    parser.add_argument("--out", type=Path, help="Write full per-question results as JSON")
    parser.add_argument("--validate", action="store_true", help="Check the dataset only; no model")
    parser.add_argument(
        "--no-warmup", action="store_true", help="Skip loading the models before timing starts"
    )
    parser.add_argument(
        "--fail-under", type=float, help="Exit 1 if result_match falls below this (0-1)"
    )
    args = parser.parse_args()

    entries = load_questions(args.questions)
    store = SqlLibraryStore.in_memory(load_ale_csv(args.library))

    problems = validate_dataset(entries, store)
    if problems:
        print("Dataset problems:\n  " + "\n  ".join(problems))
        return 1
    if args.validate:
        print(f"{len(entries)} questions OK against {len(store.all_books())} books in {args.library.name}")
        return 0

    settings = get_settings()
    model = args.model or settings.ollama_model
    url = args.url or settings.ollama_url
    router_model = args.router_model or settings.ollama_router_model or model
    provider = OllamaProvider(url, model, settings.ollama_timeout)
    router = (
        provider if router_model == model else OllamaProvider(url, router_model, settings.ollama_timeout)
    )
    if not args.no_warmup:
        print("Loading models (not timed)...", flush=True)
        try:
            warm_up(provider, *([router] if router is not provider else []))
        except httpx2.HTTPError as exc:
            print(
                f"Could not use the model ({exc}). Is it pulled? Try: "
                f"docker compose exec ollama ollama pull {model} (or {router_model})",
                file=sys.stderr,
            )
            return 2
    records = evaluate(entries, store, provider, router)
    summary = summarize(records)
    _print_report(summary, records, model, router_model)
    if args.out:
        args.out.write_text(
            json.dumps({"model": model, "router_model": router_model, "summary": summary, "records": records}, indent=2),
            encoding="utf-8",
        )
    if args.fail_under is not None and (summary["result_match"] or 0) < args.fail_under:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
