"""What the API returns. Explicit fields, so a field added to the domain ``Book`` is not exposed
until someone decides it should be (decision D5)."""

from datetime import date

from pydantic import BaseModel

from l_space.library.models import Book, SeriesEntry


class BookOut(BaseModel):
    """A book as the API shows it: the opaque ``public_id`` instead of the database ``id``, and
    no ``raw`` source row. ``source_id`` (the ASIN) is an ordinary field, never part of a URL."""

    public_id: str
    source_id: str
    title: str
    full_title: str | None = None
    subtitle: str | None = None
    authors: list[str] = []
    narrators: list[str] = []
    series: list[SeriesEntry] = []
    tags: list[str] = []
    parent_category: str | None = None
    child_category: str | None = None
    duration_minutes: int | None = None
    finished: bool | None = None
    minutes_left: int | None = None
    my_rating: float | None = None
    community_rating: float | None = None
    rating_count: int | None = None
    publisher: str | None = None
    format: str | None = None
    release_date: date | None = None
    plus_catalog: bool = False
    unavailable: bool = False

    @classmethod
    def from_book(cls, book: Book) -> "BookOut":
        if book.public_id is None:
            raise ValueError("Only books read from a library have a public id")
        return cls(**book.model_dump(include=set(cls.model_fields)))
