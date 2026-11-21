"""Runs a QueryPlan with plain code. No model is involved here, so every count and filter is exact."""

from dataclasses import dataclass, field
from typing import Any

from l_space.ask.plan import QueryPlan
from l_space.library import queries
from l_space.library.models import Book


@dataclass
class Result:
    op: str
    total: int  # how many items matched, before the row limit
    rows: list[dict[str, Any]] = field(default_factory=list)  # what is shown

    @property
    def truncated(self) -> bool:
        return self.total > len(self.rows) > 0


def format_minutes(minutes: int | None) -> str | None:
    if minutes is None:
        return None
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m" if hours else f"{mins}m"


def _position(value: float | None, end: float | None) -> str:
    if value is None:
        return ""
    text = str(int(value)) if value.is_integer() else str(value)
    if end is not None:
        text += f"-{int(end)}"
    return f" #{text}"


def _book_row(book: Book) -> dict[str, Any]:
    row: dict[str, Any] = {
        "title": book.title,
        "authors": ", ".join(book.authors),
        "narrators": ", ".join(book.narrators),
        "series": ", ".join(f"{s.name}{_position(s.position, s.position_end)}" for s in book.series),
        "length": format_minutes(book.duration_minutes),
    }
    if book.release_date:
        row["year"] = book.release_date.year
    if book.my_rating is not None:
        row["my_rating"] = book.my_rating
    if book.finished is False and book.minutes_left is not None:
        row["time_left"] = format_minutes(book.minutes_left)
    return {k: v for k, v in row.items() if v not in ("", None)}


def _sorted(books: list[Book], sort: str) -> list[Book]:
    def by(key, reverse=False):
        # Unknown values always sort last, in either direction.
        known = [b for b in books if key(b) is not None]
        unknown = [b for b in books if key(b) is None]
        return sorted(known, key=key, reverse=reverse) + unknown

    if sort == "longest":
        return by(lambda b: b.duration_minutes, reverse=True)
    if sort == "shortest":
        return by(lambda b: b.duration_minutes)
    if sort == "newest":
        return by(lambda b: b.release_date, reverse=True)
    if sort == "oldest":
        return by(lambda b: b.release_date)
    return sorted(books, key=lambda b: b.title.casefold())


def _filters(plan: QueryPlan) -> dict[str, Any]:
    return {
        "author": plan.author,
        "narrator": plan.narrator,
        "series": plan.series,
        "category": plan.category,
        "min_minutes": plan.min_minutes,
        "max_minutes": plan.max_minutes,
        "min_my_rating": plan.min_my_rating,
    }


def execute(plan: QueryPlan, books: list[Book]) -> Result:
    op = plan.op
    if op == "list_books":
        matched = _sorted(queries.filter_books(books, **_filters(plan)), plan.sort)
        return Result(op, len(matched), [_book_row(b) for b in matched[: plan.limit]])

    if op == "count_books":
        return Result(op, len(queries.filter_books(books, **_filters(plan))))

    if op in ("narrator_counts", "author_counts"):
        scoped = queries.filter_books(books, **_filters(plan))
        pairs = (
            queries.narrator_counts(scoped) if op == "narrator_counts" else queries.author_counts(scoped)
        )
        label = "narrator" if op == "narrator_counts" else "author"
        return Result(op, len(pairs), [{label: n, "books": c} for n, c in pairs[: plan.limit]])

    if op == "series_gaps":
        gaps = queries.series_gaps(books)
        if plan.series:
            gaps = [g for g in gaps if plan.series.casefold() in g["series"].casefold()]
        rows = [
            {"series": g["series"], "owned_books": g["owned"], "missing_books": g["missing"]}
            for g in gaps[: plan.limit]
        ]
        return Result(op, len(gaps), rows)

    if op == "in_progress":
        started = queries.in_progress(queries.filter_books(books, **_filters(plan)))
        return Result(op, len(started), [_book_row(b) for b in started[: plan.limit]])

    return Result(op, 0)


def render(result: Result) -> str:
    """Plain, deterministic text for a result. Used when the model's wording can't be trusted."""
    n = result.total
    if result.op == "count_books":
        return f"{n} book{'s' if n != 1 else ''} match."
    if n == 0:
        return "Nothing in your library matches that."

    lines = []
    for r in result.rows:
        if result.op in ("list_books", "in_progress"):
            line = f"- {r['title']}"
            if r.get("authors"):
                line += f" by {r['authors']}"
            extras = [r.get("length"), r.get("time_left") and f"{r['time_left']} left"]
            extras = [e for e in extras if e]
            if extras:
                line += f" ({', '.join(extras)})"
        elif result.op in ("narrator_counts", "author_counts"):
            name = r.get("narrator") or r.get("author")
            line = f"- {name}: {r['books']} book{'s' if r['books'] != 1 else ''}"
        else:  # series_gaps
            missing = ", ".join(str(m) for m in r["missing_books"])
            line = f"- {r['series']}: missing book {missing}"
        lines.append(line)

    header = f"Showing {len(result.rows)} of {n}:" if result.truncated else f"{n} result{'s' if n != 1 else ''}:"
    return "\n".join([header, *lines])
