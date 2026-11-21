"""Deterministic library questions. Pure functions over Book lists, easy to test."""

from collections import Counter, defaultdict
from collections.abc import Iterable

from l_space.library.models import Book


def _ranked(counter: Counter[str]) -> list[tuple[str, int]]:
    """Most frequent first; ties broken alphabetically so results are stable."""
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))


def _contains(needle: str, values: Iterable[str]) -> bool:
    """Case-insensitive substring match, so 'pratchett' finds 'Terry Pratchett'."""
    n = needle.casefold().strip()
    return any(n in v.casefold() for v in values)


def filter_books(
    books: list[Book],
    *,
    author: str | None = None,
    narrator: str | None = None,
    series: str | None = None,
    category: str | None = None,
    min_minutes: int | None = None,
    max_minutes: int | None = None,
    min_my_rating: float | None = None,
) -> list[Book]:
    """Audiobooks matching every given filter (podcasts and the like are always excluded).

    Books with an unknown length or no rating of yours never match a length or rating filter.
    """
    out = []
    for b in books:
        if not b.is_audiobook:
            continue
        if author and not _contains(author, b.authors):
            continue
        if narrator and not _contains(narrator, b.narrators):
            continue
        if series and not _contains(series, (s.name for s in b.series)):
            continue
        if category and not _contains(
            category, [b.parent_category or "", b.child_category or "", *b.tags]
        ):
            continue
        if min_minutes is not None and (b.duration_minutes is None or b.duration_minutes < min_minutes):
            continue
        if max_minutes is not None and (b.duration_minutes is None or b.duration_minutes > max_minutes):
            continue
        if min_my_rating is not None and (b.my_rating is None or b.my_rating < min_my_rating):
            continue
        out.append(b)
    return out


def narrator_counts(books: list[Book], min_my_rating: float | None = None) -> list[tuple[str, int]]:
    """Narrators ranked by number of audiobooks, optionally only counting books you rated highly."""
    counter: Counter[str] = Counter()
    for b in books:
        if not b.is_audiobook:
            continue
        if min_my_rating is not None and (b.my_rating is None or b.my_rating < min_my_rating):
            continue
        counter.update(b.narrators)
    return _ranked(counter)


def author_counts(books: list[Book]) -> list[tuple[str, int]]:
    """Authors ranked by number of audiobooks."""
    counter: Counter[str] = Counter()
    for b in books:
        if b.is_audiobook:
            counter.update(b.authors)
    return _ranked(counter)


def series_gaps(books: list[Book]) -> list[dict]:
    """Numbered series where you own later books but not every earlier one.

    Only whole-number positions count (so 'book 22.5' never creates a gap), and an omnibus
    entry such as 'book 1-7' covers every number in its range. Unnumbered umbrella series
    like 'The Cosmere' are ignored. This reflects what is in the library, not the full
    series: it cannot know about books after the highest one you own.
    """
    owned: dict[str, set[int]] = defaultdict(set)
    for b in books:
        for s in b.series:
            if s.position is None or not s.position.is_integer():
                continue
            last = int(s.position_end) if s.position_end is not None else int(s.position)
            owned[s.name].update(range(int(s.position), last + 1))

    result = []
    for name, numbers in owned.items():
        missing = [n for n in range(1, max(numbers) + 1) if n not in numbers]
        if missing:
            result.append({"series": name, "owned": sorted(numbers), "missing": missing})
    return sorted(result, key=lambda r: r["series"])


def in_progress(books: list[Book]) -> list[Book]:
    """Books marked as started but not finished."""
    return [b for b in books if b.finished is False]
