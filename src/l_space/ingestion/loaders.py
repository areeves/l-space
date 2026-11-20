"""Loader for the CSV export of the Audible Library Extractor (ALE) browser extension.

Only the columns l-space uses are read; the complete source row is kept in ``Book.raw``.
Reading the file with ``csv`` also handles the multi-line blurbs ALE exports.
"""

import csv
from collections.abc import Iterator
from datetime import date
from pathlib import Path

from l_space.ingestion.normalize import (
    clean_text,
    parse_duration_minutes,
    parse_progress,
    parse_series,
    split_list,
    split_people,
)
from l_space.library.models import Book

# Blurbs and summaries are long publisher text; allow large fields.
csv.field_size_limit(10**9)


def _text(row: dict[str, str], key: str) -> str | None:
    return clean_text(row.get(key)) or None


def _float(row: dict[str, str], key: str) -> float | None:
    try:
        return float(row[key]) if row.get(key) else None
    except ValueError:
        return None


def _int(row: dict[str, str], key: str) -> int | None:
    value = _float(row, key)
    return int(value) if value is not None else None


def _date(row: dict[str, str], key: str) -> date | None:
    try:
        return date.fromisoformat(row[key]) if row.get(key) else None
    except ValueError:
        return None


def _flag(row: dict[str, str], key: str) -> bool:
    return clean_text(row.get(key)).lower() == "true"


def load_ale_csv(path: Path) -> Iterator[Book]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            asin, title = _text(row, "ASIN"), _text(row, "Title Short") or _text(row, "Title")
            if not asin or not title:
                continue
            finished, minutes_left = parse_progress(row.get("Progress"))
            yield Book(
                source_id=asin,
                title=title,
                full_title=_text(row, "Title"),
                subtitle=_text(row, "Subtitle"),
                authors=split_people(row.get("Authors")),
                narrators=split_people(row.get("Narrators")),
                series=parse_series(row.get("Series")),
                tags=split_list(row.get("Tags")),
                parent_category=_text(row, "Parent Category"),
                child_category=_text(row, "Child Category"),
                duration_minutes=parse_duration_minutes(row.get("Length")),
                finished=finished,
                minutes_left=minutes_left,
                my_rating=_float(row, "My Rating"),
                community_rating=_float(row, "Rating"),
                rating_count=_int(row, "Ratings"),
                publisher=_text(row, "Publishers"),
                format=_text(row, "Format"),
                release_date=_date(row, "Release Date"),
                plus_catalog=_flag(row, "From Plus Catalog"),
                unavailable=_flag(row, "Unavailable"),
                raw=dict(row),
            )
