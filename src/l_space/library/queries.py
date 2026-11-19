"""Deterministic library questions. Pure functions over Book lists, easy to test."""

from collections import Counter, defaultdict

from l_space.library.models import Book


def narrator_counts(books: list[Book], min_rating: float | None = None) -> list[tuple[str, int]]:
    """Narrators ranked by number of books, optionally only counting well-rated ones."""
    counter: Counter[str] = Counter()
    for b in books:
        if min_rating is not None and (b.rating is None or b.rating < min_rating):
            continue
        counter.update(b.narrators)
    return counter.most_common()


def stalled_series(books: list[Book]) -> list[dict]:
    """Series with at least one finished book and at least one unstarted book still waiting.

    Returns the next unstarted book (lowest position) for each such series.
    """
    by_series: dict[str, list[Book]] = defaultdict(list)
    for b in books:
        if b.series:
            by_series[b.series].append(b)

    result = []
    for name, entries in by_series.items():
        finished = [b for b in entries if b.finished]
        waiting = sorted(
            (b for b in entries if not b.started),
            key=lambda b: (b.series_position is None, b.series_position or 0),
        )
        if finished and waiting:
            result.append(
                {
                    "series": name,
                    "finished": len(finished),
                    "unstarted": len(waiting),
                    "next_up": waiting[0].title,
                }
            )
    return sorted(result, key=lambda r: r["series"])
