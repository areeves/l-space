"""The full question-answering pipeline: route -> execute -> phrase -> ground-check."""

import json
import time
from typing import Any

import httpx
from pydantic import BaseModel, Field

from l_space.ask.executor import Result, execute, render
from l_space.ask.grounding import ungrounded_terms
from l_space.ask.plan import QueryPlan
from l_space.ask.router import route
from l_space.library.models import Book
from l_space.models.provider import ModelProvider

WRITER_SYSTEM = """\
You answer a question about a personal audiobook library using ONLY the JSON data provided.
Be concise: one to three sentences, or a short list. Do not mention any title, name, or number
that is not in the data. If "shown" is smaller than "total_matching", say the list is partial.
"""

_WRITER_MAX_TOKENS = 300  # answers are one to three sentences or a short list

CAPABILITIES = (
    "I can list or count books (by author, narrator, series, category, length or your "
    "rating), rank narrators or authors, find gaps in numbered series, and show books "
    "you're partway through."
)


class Answer(BaseModel):
    question: str
    text: str
    plan: QueryPlan
    total: int = 0
    rows: list[dict[str, Any]] = Field(default_factory=list)
    abstained: bool = False
    # Did the model's draft pass the grounding check? None when no draft was written.
    grounded: bool | None = None
    # True when the model's wording was replaced by deterministic text.
    used_fallback: bool = False
    ungrounded_terms: list[str] = Field(default_factory=list)
    router_error: str | None = None
    timings_ms: dict[str, float] = Field(default_factory=dict)


def _evidence(question: str, result: Result) -> str:
    data = {"total_matching": result.total, "shown": len(result.rows), "rows": result.rows}
    return f"{question}\n{json.dumps(data, ensure_ascii=False)}"


def _writer_prompt(question: str, result: Result) -> str:
    data = {"total_matching": result.total, "shown": len(result.rows), "rows": result.rows}
    return f"Question: {question}\n\nData:\n{json.dumps(data, ensure_ascii=False)}\n\nAnswer:"


def answer_question(
    question: str,
    books: list[Book],
    provider: ModelProvider,
    router: ModelProvider | None = None,
) -> Answer:
    """``provider`` writes the answer; ``router`` (default: the same model) writes the plan."""
    timings: dict[str, float] = {}

    started = time.perf_counter()
    plan, router_error = route(question, router or provider)
    timings["route"] = (time.perf_counter() - started) * 1000

    def done(text: str, result: Result | None = None, **extra: Any) -> Answer:
        return Answer(
            question=question,
            text=text,
            plan=plan,
            total=result.total if result else 0,
            rows=result.rows if result else [],
            router_error=router_error,
            timings_ms={k: round(v, 1) for k, v in timings.items()},
            **extra,
        )

    if plan.op == "unsupported":
        parts = ["I can't answer that from your library data.", plan.reason, CAPABILITIES]
        return done(" ".join(p for p in parts if p), abstained=True)

    result = execute(plan, books)
    if result.total == 0:
        return done(render(result), result)

    started = time.perf_counter()
    try:
        draft = provider.generate(
            _writer_prompt(question, result), system=WRITER_SYSTEM, max_tokens=_WRITER_MAX_TOKENS
        ).strip()
    except httpx.HTTPError:
        draft = ""
    timings["write"] = (time.perf_counter() - started) * 1000

    if not draft:
        return done(render(result), result, used_fallback=True)

    flagged = ungrounded_terms(draft, _evidence(question, result), len(result.rows))
    if flagged:
        return done(
            render(result), result, grounded=False, used_fallback=True, ungrounded_terms=flagged
        )
    return done(draft, result, grounded=True)
