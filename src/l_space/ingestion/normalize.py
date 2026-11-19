"""Normalization helpers. Cleaned values are stored next to the originals, never instead of them."""

import re

_HOURS = re.compile(r"(\d+)\s*(?:hrs?|hours?|h)\b", re.IGNORECASE)
_MINS = re.compile(r"(\d+)\s*(?:mins?|minutes?|m)\b", re.IGNORECASE)
_SERIES_POS = re.compile(r"^(?P<name>.+?)[,\s]+(?:book|vol(?:ume)?\.?|#)\s*(?P<pos>\d+(?:\.\d+)?)$", re.IGNORECASE)
_WS = re.compile(r"\s+")


def clean_text(value: str | None) -> str:
    """Collapse whitespace and strip. Case is preserved."""
    return _WS.sub(" ", (value or "")).strip()


def split_people(value: str | None) -> list[str]:
    """Split a 'A; B' or 'A, B' style credit list into individual names."""
    parts = re.split(r"\s*[;|]\s*|\s*,\s*(?![^()]*\))", clean_text(value))
    return [p for p in parts if p]


def parse_duration_minutes(value: str | float | None) -> int | None:
    """Accept '12 hrs and 30 mins', '45 mins', '90', or a number of minutes."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    h, m = _HOURS.search(text), _MINS.search(text)
    if not (h or m):
        return None
    return (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)


def parse_series(series: str | None, position: str | float | None = None) -> tuple[str | None, float | None]:
    """Return (series_name, position). Handles 'Name, Book 3' embedded in the series field."""
    text = clean_text(series)
    if not text:
        return None, None
    pos: float | None = None
    if position not in (None, ""):
        try:
            pos = float(position)
        except (TypeError, ValueError):
            pos = None
    match = _SERIES_POS.match(text)
    if match:
        text = clean_text(match.group("name"))
        pos = pos if pos is not None else float(match.group("pos"))
    return text, pos
