"""CSV loader with a configurable column mapping.

The real Audible export format is not assumed here: adjust COLUMN_MAP (or pass your own)
once you have looked at your own export. The untouched source row is kept in ``raw``.
"""

import csv
from collections.abc import Iterator
from pathlib import Path

from l_space.ingestion.normalize import (
    clean_text,
    parse_duration_minutes,
    parse_series,
    split_people,
)
from l_space.library.models import Book

# canonical field -> column header in the source file
COLUMN_MAP: dict[str, str] = {
    "source_id": "asin",
    "title": "title",
    "authors": "authors",
    "narrators": "narrators",
    "series": "series",
    "series_position": "series_position",
    "duration": "minutes",
    "rating": "rating",
    "progress_pct": "progress_pct",
}


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def load_csv(path: Path, column_map: dict[str, str] | None = None) -> Iterator[Book]:
    cols = {**COLUMN_MAP, **(column_map or {})}
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            def get(key: str, row: dict[str, str] = row) -> str | None:
                return row.get(cols[key])

            series, position = parse_series(get("series"), get("series_position"))
            yield Book(
                source_id=clean_text(get("source_id")) or None,
                title=clean_text(get("title")),
                authors=split_people(get("authors")),
                narrators=split_people(get("narrators")),
                series=series,
                series_position=position,
                duration_minutes=parse_duration_minutes(get("duration")),
                rating=_float(get("rating")),
                progress_pct=_float(get("progress_pct")),
                raw=dict(row),
            )
