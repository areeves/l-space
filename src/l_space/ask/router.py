"""Turns a natural-language question into a QueryPlan using the model, with one retry."""

from pydantic import ValidationError

from l_space.ask.plan import QueryPlan, plan_schema
from l_space.models.provider import ModelProvider

SYSTEM_PROMPT = """\
You translate questions about a personal audiobook library into ONE JSON query plan.
Reply with JSON only.

The library records, for each audiobook: title, authors, narrators, series and position,
length, release year, category and tags, finished or time left, and the user's own 1-5 star
rating (often missing). It has NO prices, wishlists, plots, reviews, recommendations, or
facts about the world.

Choose exactly one "op":
- list_books: list books, optionally filtered and sorted
- count_books: count books matching the filters
- narrator_counts: rank narrators by how many books they narrated
- author_counts: rank authors by how many books they wrote
- series_gaps: numbered series where later books are owned but earlier ones are missing
- in_progress: books that were started but not finished
- unsupported: anything the library data cannot answer

Filters (use null when the question does not ask for them):
- author is who WROTE a book ("by", "written by"). narrator is who READ it ("narrated by",
  "read by"). Never put a narrator in author or an author in narrator.
- series, category.
- min_minutes: "longer than", "over", "at least". max_minutes: "shorter than", "under",
  "less than", "at most". Convert hours to minutes (2 hours = 120).
- min_my_rating: the user's own star rating, "N stars or more".
- sort is one of: title, longest, shortest, newest, oldest.
Never invent filters the question did not ask for.

Use "unsupported" when the question needs something the library does not record
(see the list above), and put a short explanation in "reason". Leave "reason" null for every
other op.

Examples:
Q: How many books by Jane Marlow do I have?
A: {"op": "count_books", "author": "Jane Marlow"}
Q: Which books are over 20 hours?
A: {"op": "list_books", "min_minutes": 1200, "sort": "longest"}
Q: Which books are under 8 hours?
A: {"op": "list_books", "max_minutes": 480, "sort": "shortest"}
Q: Show me everything narrated by Tom Brook.
A: {"op": "list_books", "narrator": "Tom Brook"}
Q: Show me the Iron Court series.
A: {"op": "list_books", "series": "Iron Court"}
Q: Which series have holes in them?
A: {"op": "series_gaps"}
Q: Which narrators do I own the most books from?
A: {"op": "narrator_counts"}
Q: Which authors have I given three stars or more?
A: {"op": "author_counts", "min_my_rating": 3}
Q: Which books have I started but not finished?
A: {"op": "in_progress"}
Q: What happens at the end of Dragonfall?
A: {"op": "unsupported", "reason": "The library has no plot information."}
Q: Who is the best narrator in the world?
A: {"op": "unsupported", "reason": "That is an opinion, not something the library records."}
"""

_FALLBACK_REASON = "I couldn't turn that into a query."
_MAX_TOKENS = 300  # a plan is ~100 tokens; the cap stops a runaway generation on CPU


def route(question: str, provider: ModelProvider) -> tuple[QueryPlan, str | None]:
    """Return (plan, error). ``error`` is set only when both attempts produced an invalid plan,
    in which case the plan is an ``unsupported`` one so callers abstain instead of guessing.

    A plan that names a real operation but also carries a ``reason`` is contradictory (the
    model explained why it can't answer, then picked an operation anyway), so it is treated as
    ``unsupported``. Declining is the safe way to resolve the contradiction.
    """
    prompt = f"Q: {question}\nA:"
    error: str | None = None
    for attempt in range(2):
        note = "" if attempt == 0 else f"\n(Your previous reply was invalid: {error}. JSON only.)"
        raw = provider.generate(
            prompt + note, system=SYSTEM_PROMPT, schema=plan_schema(), max_tokens=_MAX_TOKENS
        )
        try:
            plan = QueryPlan.model_validate_json(raw)
        except ValidationError as exc:
            first = exc.errors()[0]
            error = f"{'.'.join(map(str, first['loc'])) or 'reply'}: {first['msg']}"
            continue
        if plan.op != "unsupported" and plan.reason:
            plan = QueryPlan(op="unsupported", reason=plan.reason)
        return plan, None
    return QueryPlan(op="unsupported", reason=_FALLBACK_REASON), error
