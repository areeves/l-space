"""The structured query the model is allowed to produce.

Deliberately flat and small: local models follow a short, flat schema far more reliably than
free-form tool calling. Validators are lenient about harmless noise (blank strings for unused
filters, an out-of-range limit) and strict about the operation and sort names.
"""

from typing import Literal

from pydantic import BaseModel, field_validator

from l_space.library.filters import BookFilter

Op = Literal[
    "list_books",
    "count_books",
    "narrator_counts",
    "author_counts",
    "series_gaps",
    "in_progress",
    "unsupported",
]
Sort = Literal["title", "longest", "shortest", "newest", "oldest"]

_BLANK = {"", "null", "none", "n/a", "any", "all"}


class QueryPlan(BaseModel):
    op: Op
    author: str | None = None
    narrator: str | None = None
    series: str | None = None
    category: str | None = None
    min_minutes: int | None = None
    max_minutes: int | None = None
    min_my_rating: float | None = None
    sort: Sort = "title"
    limit: int = 20
    reason: str | None = None  # why a question is unsupported

    @field_validator("author", "narrator", "series", "category", "reason", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if v.casefold() in _BLANK:
                return None
        return v

    @field_validator("min_minutes", "max_minutes", "min_my_rating", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        return None if v in ("", None) else v

    @field_validator("limit", mode="before")
    @classmethod
    def _clamp_limit(cls, v):
        try:
            return min(max(int(v), 1), 50)
        except (TypeError, ValueError):
            return 20

    def to_filter(self) -> BookFilter:
        """The filter fields of this plan. Follows ``BookFilter``, so a new filter needs no edit here."""
        return BookFilter(**{name: getattr(self, name) for name in BookFilter.model_fields})


def plan_schema() -> dict:
    """JSON schema handed to Ollama so decoding is constrained to a valid plan."""
    return QueryPlan.model_json_schema()
