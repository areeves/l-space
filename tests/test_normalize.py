import pytest

from l_space.ingestion.normalize import (
    parse_duration_minutes,
    parse_progress,
    parse_series,
    split_list,
    split_people,
)


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("16h 54m ", 1014),  # ALE format, with trailing space
        ("45m ", 45),
        ("12 hrs and 30 mins", 750),
        ("11 hrs", 660),
        ("90", 90),
        ("", None),
        ("unknown", None),
    ],
)
def test_duration_formats(text, minutes):
    assert parse_duration_minutes(text) == minutes


def test_progress():
    assert parse_progress("Finished") == (True, None)
    assert parse_progress("10h 48m left") == (False, 648)
    assert parse_progress("") == (None, None)


def test_single_series():
    [entry] = parse_series("The Bridge Kingdom Series (book 4)")
    assert (entry.name, entry.position, entry.position_end) == ("The Bridge Kingdom Series", 4.0, None)


def test_multiple_series_with_unnumbered_umbrella():
    entries = parse_series("Elantris (book 1), The Cosmere (book )")
    assert [(e.name, e.position) for e in entries] == [("Elantris", 1.0), ("The Cosmere", None)]


def test_bare_trailing_series_name():
    entries = parse_series("Elantris (book 1), The Cosmere")
    assert [(e.name, e.position) for e in entries] == [("Elantris", 1.0), ("The Cosmere", None)]
    assert [(e.name, e.position) for e in parse_series("The Cosmere")] == [("The Cosmere", None)]


def test_decimal_and_range_positions():
    [decimal] = parse_series("Discworld (book 22.5)")
    assert decimal.position == 22.5
    [omnibus] = parse_series("Sherlock Holmes (book 1-6)")
    assert (omnibus.position, omnibus.position_end) == (1.0, 6.0)


def test_series_name_containing_parentheses():
    entries = parse_series(
        "The Chronicles of Narnia (Author's Preferred Order) (book 1-7), "
        "The Chronicles of Narnia (Publication Order) (book 1-7)"
    )
    assert [e.name for e in entries] == [
        "The Chronicles of Narnia (Author's Preferred Order)",
        "The Chronicles of Narnia (Publication Order)",
    ]


def test_position_with_trailing_note():
    [entry] = parse_series("Night Angel (book 1, Kylar Chronicles)")
    assert (entry.name, entry.position) == ("Night Angel", 1.0)


def test_empty_series():
    assert parse_series("") == []
    assert parse_series(None) == []


def test_people_and_roles():
    assert split_people("Summer Morton, Michael Pauley") == ["Summer Morton", "Michael Pauley"]
    assert split_people("Eiko Kadono, Emily Balistrieri - translator") == [
        "Eiko Kadono",
        "Emily Balistrieri",
    ]
    assert split_people("") == []


def test_split_list():
    assert split_list("Epic, Fantasy,  War") == ["Epic", "Fantasy", "War"]
