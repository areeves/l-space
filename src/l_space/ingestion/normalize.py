"""Normalization helpers. Cleaned values are stored next to the originals, never instead of them."""

import re

from l_space.library.models import SeriesEntry

_HOURS = re.compile(r"(\d+)\s*(?:hrs?|hours?|h)\b", re.IGNORECASE)
_MINS = re.compile(r"(\d+)\s*(?:mins?|minutes?|m)\b", re.IGNORECASE)
_WS = re.compile(r"\s+")
_ROLE_SUFFIX = re.compile(r"\s+-\s+[A-Za-z ]+$")

# "Name (book 4)", "Name (book )", "Name (book 1-7)", "Name (book 1, Subtitle)"
_SERIES_ENTRY = re.compile(r"(?:^|(?<=, ))(?P<name>.+?) \(book (?P<pos>[^)]*)\)(?=, |$)")
_POSITION = re.compile(r"^\s*(\d+(?:\.\d+)?)(?:\s*-\s*(\d+(?:\.\d+)?))?")


def clean_text(value: str | None) -> str:
    """Collapse whitespace and strip. Case is preserved."""
    return _WS.sub(" ", (value or "")).strip()


def split_people(value: str | None) -> list[str]:
    """Split a credit list like 'A, B - translator' into names, dropping role suffixes.

    The original string (roles included) stays available in ``Book.raw``.
    """
    parts = re.split(r"\s*[;|]\s*|\s*,\s*(?![^()]*\))", clean_text(value))
    return [name for p in parts if (name := _ROLE_SUFFIX.sub("", p).strip())]


def split_list(value: str | None) -> list[str]:
    """Split a plain comma-separated list such as Tags."""
    return [p.strip() for p in clean_text(value).split(",") if p.strip()]


def parse_duration_minutes(value: str | float | None) -> int | None:
    """Accept '16h 54m ', '45m', '12 hrs and 30 mins', '90', or a number of minutes."""
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


def parse_progress(value: str | None) -> tuple[bool | None, int | None]:
    """Return (finished, minutes_left). 'Finished' -> (True, None); '10h 48m left' -> (False, 648)."""
    text = clean_text(value)
    if not text:
        return None, None
    if text.lower() == "finished":
        return True, None
    if text.lower().endswith("left"):
        return False, parse_duration_minutes(text)
    return None, None


def parse_series(value: str | None) -> list[SeriesEntry]:
    """Parse ALE's series field into entries.

    Handles several memberships ('Discworld (book 8), Discworld: Guards (book 1)'),
    unnumbered ones ('The Cosmere (book )' or a bare name), decimals ('book 22.5'), and
    omnibus ranges ('book 1-7').
    """
    text = clean_text(value)
    if not text:
        return []
    entries: list[SeriesEntry] = []
    end = 0
    for match in _SERIES_ENTRY.finditer(text):
        end = match.end()
        pos = _POSITION.match(match.group("pos"))
        entries.append(
            SeriesEntry(
                name=clean_text(match.group("name")),
                position=float(pos.group(1)) if pos else None,
                position_end=float(pos.group(2)) if pos and pos.group(2) else None,
            )
        )
    tail = text[end:].lstrip(", ").strip()
    if tail:  # a trailing membership with no "(book ...)" part
        entries.append(SeriesEntry(name=tail))
    return entries
