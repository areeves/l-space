"""The set of filters a library question can apply, as one value.

``queries.filter_books`` stays the reference implementation of what each filter means.
Stores may override ``filter_books`` / ``count_books`` with something faster, but must agree
with it (see tests/test_store_filters.py).
"""

from pydantic import BaseModel, ConfigDict


class BookFilter(BaseModel):
    model_config = ConfigDict(frozen=True)

    author: str | None = None
    narrator: str | None = None
    series: str | None = None
    category: str | None = None
    min_minutes: int | None = None
    max_minutes: int | None = None
    min_my_rating: float | None = None

    def as_kwargs(self) -> dict:
        """Keyword arguments for ``queries.filter_books``."""
        return self.model_dump()
