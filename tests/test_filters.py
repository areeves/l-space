from l_space.library import queries


def titles(books):
    return sorted(b.title for b in books)


def test_filters_by_name_case_insensitive_substring(books):
    assert titles(queries.filter_books(books, author="verrow")) == [
        "The Ash Regent",
        "The Cinder Throne",
        "The Ember Crown",
    ]
    assert len(queries.filter_books(books, narrator="JOSS")) == 3


def test_series_filter_matches_any_membership(books):
    # 'Saltmarch' matches both 'The Saltmarch Cycle' and 'Saltmarch Collected'
    assert titles(queries.filter_books(books, series="saltmarch")) == [
        "A Lantern for the Drowned",
        "The Drowned Archive",
        "The Saltmarch Omnibus",
    ]


def test_length_filters_exclude_unknown_lengths(books):
    long_ones = queries.filter_books(books, min_minutes=900)
    assert "The Hollow Lantern" not in titles(long_ones)
    assert len(long_ones) == 5
    assert titles(queries.filter_books(books, max_minutes=60)) == ["Between the Tides"]


def test_podcasts_are_never_returned(books):
    assert all(b.is_audiobook for b in queries.filter_books(books))
    assert len(queries.filter_books(books)) == 11


def test_rating_filter_needs_my_rating(books):
    assert titles(queries.filter_books(books, min_my_rating=5)) == [
        "A Lantern for the Drowned",
        "The Cartographer's Debt",
        "The Ember Crown",
    ]


def test_filters_combine(books):
    assert titles(queries.filter_books(books, author="Maren Oakley", max_minutes=700)) == [
        "A Lantern for the Drowned",
        "Between the Tides",
    ]


def test_author_counts_and_stable_ties(books):
    counts = queries.author_counts(books)
    assert counts[0] == ("Maren Oakley", 4)
    assert ("Ana Pell", 1) in counts
    assert queries.narrator_counts(books)[:2] == [("Corin Ashdale", 4), ("Petra Lindqvist", 4)]
