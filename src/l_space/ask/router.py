"""Turns a natural-language question into a QueryPlan using the model, with one retry."""

from pydantic import ValidationError

from l_space.ask.plan import QueryPlan, plan_schema
from l_space.models.provider import ModelProvider

SYSTEM_PROMPT = """\
You translate questions about a personal audiobook library into ONE JSON query plan.
Reply with JSON only. Choose exactly one "op":
- list_books: list books, optionally filtered and sorted
- count_books: count books matching the filters
- narrator_counts: rank narrators by how many books they narrated
- author_counts: rank authors by how many books they wrote
- series_gaps: numbered series where later books are owned but earlier ones are missing
- in_progress: books that were started but not finished
- unsupported: anything the library data cannot answer (recommendations, plot summaries,
  opinions, wishlists, facts about the world, edits or deletions, small talk)

Filters (use null when the question does not ask for them): author, narrator, series,
category, min_minutes, max_minutes, min_my_rating (the user's own 1-5 star rating).
Convert hours to minutes (2 hours = 120). "sort" is one of: title, longest, shortest,
newest, oldest. For "unsupported", put a short explanation in "reason".
Never invent filters the question did not ask for.

Examples:
Q: How many books by Jane Marlow do I have?
A: {"op": "count_books", "author": "Jane Marlow"}
Q: Which books are over 20 hours?
A: {"op": "list_books", "min_minutes": 1200, "sort": "longest"}
Q: Which series have holes in them?
A: {"op": "series_gaps"}
Q: Show me the Iron Court series.
A: {"op": "list_books", "series": "Iron Court"}
Q: Which narrators do I own the most books from?
A: {"op": "narrator_counts"}
Q: What happens at the end of Dragonfall?
A: {"op": "unsupported", "reason": "The library has no plot information."}
"""

_FALLBACK_REASON = "I couldn't turn that into a query."


def route(question: str, provider: ModelProvider) -> tuple[QueryPlan, str | None]:
    """Return (plan, error). ``error`` is set only when both attempts produced an invalid plan,
    in which case the plan is an ``unsupported`` one so callers abstain instead of guessing."""
    prompt = f"Q: {question}\nA:"
    error: str | None = None
    for attempt in range(2):
        note = "" if attempt == 0 else f"\n(Your previous reply was invalid: {error}. JSON only.)"
        raw = provider.generate(prompt + note, system=SYSTEM_PROMPT, schema=plan_schema())
        try:
            return QueryPlan.model_validate_json(raw), None
        except ValidationError as exc:
            first = exc.errors()[0]
            error = f"{'.'.join(map(str, first['loc'])) or 'reply'}: {first['msg']}"
    return QueryPlan(op="unsupported", reason=_FALLBACK_REASON), error
