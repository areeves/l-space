from pydantic import BaseModel, Field


class Book(BaseModel):
    """One library entry. ``raw`` preserves the source row exactly as imported."""

    id: int | None = None
    source_id: str | None = None
    title: str
    authors: list[str] = Field(default_factory=list)
    narrators: list[str] = Field(default_factory=list)
    series: str | None = None
    series_position: float | None = None
    duration_minutes: int | None = None
    rating: float | None = None
    progress_pct: float | None = None
    raw: dict[str, str] = Field(default_factory=dict)

    @property
    def finished(self) -> bool:
        return (self.progress_pct or 0) >= 95

    @property
    def started(self) -> bool:
        return (self.progress_pct or 0) > 0
