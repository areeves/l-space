"""Deterministic library questions. Pure functions over Book lists, easy to test."""

from collections import Counter, defaultdict

from l_space.library.models import Book


def narrator_counts(books: list[Book], min_my_rating: float | None = None) -> list[tuple[str, int]]:
    """Narrators ranked by number of audiobooks, optionally only counting books you rated highly."""
    counter: Counter[str] = Counter()
    for b in books:
        if not b.is_audiobook:
            continue
        if min_my_rating is not None and (b.my_rating is None or b.my_rating < min_my_rating):
            continue
        counter.update(b.narrators)
    return counter.most_common()


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
