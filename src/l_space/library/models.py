from datetime import date

from pydantic import BaseModel, Field

NON_BOOK_FORMATS = {"Podcast", "Meditation"}


class SeriesEntry(BaseModel):
    """One series membership. A book can belong to several (e.g. Discworld and a sub-series)."""

    name: str
    position: float | None = None
    # Set for omnibus entries such as "book 1-7"; ``position`` is then the first number.
    position_end: float | None = None


class Book(BaseModel):
    """One library entry. ``raw`` preserves the source row exactly as imported."""

    id: int | None = None
    source_id: str  # ASIN
    title: str
    full_title: str | None = None
    subtitle: str | None = None
    authors: list[str] = Field(default_factory=list)
    narrators: list[str] = Field(default_factory=list)
    series: list[SeriesEntry] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    parent_category: str | None = None
    child_category: str | None = None
    duration_minutes: int | None = None
    finished: bool | None = None  # None = unknown
    minutes_left: int | None = None
    my_rating: float | None = None
    community_rating: float | None = None  # Audible's average, not the owner's opinion
    rating_count: int | None = None
    publisher: str | None = None
    format: str | None = None
    release_date: date | None = None
    plus_catalog: bool = False
    unavailable: bool = False
    raw: dict[str, str] = Field(default_factory=dict)

    @property
    def is_audiobook(self) -> bool:
        return self.format not in NON_BOOK_FORMATS
