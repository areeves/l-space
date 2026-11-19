from l_space.ingestion.normalize import parse_duration_minutes, parse_series, split_people


def test_duration_formats():
    assert parse_duration_minutes("12 hrs and 30 mins") == 750
    assert parse_duration_minutes("45 mins") == 45
    assert parse_duration_minutes("11 hrs") == 660
    assert parse_duration_minutes("90") == 90
    assert parse_duration_minutes("") is None
    assert parse_duration_minutes("unknown") is None


def test_series_embedded_position():
    assert parse_series("Ashfall Cycle, Book 1") == ("Ashfall Cycle", 1.0)
    assert parse_series("Ashfall Cycle", "2") == ("Ashfall Cycle", 2.0)
    assert parse_series("") == (None, None)


def test_explicit_position_wins():
    assert parse_series("Tidewatch, Book 1", "3") == ("Tidewatch", 3.0)


def test_split_people():
    assert split_people("Petra Lindqvist; Joss Harlan") == ["Petra Lindqvist", "Joss Harlan"]
    assert split_people("") == []
