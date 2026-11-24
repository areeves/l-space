"""Rows the sample CSV does not cover, shared by the store tests."""

from datetime import date

from l_space.library.models import Book, SeriesEntry

# Awkward rows the sample CSV doesn't cover: non-ASCII case folding, a missing format,
# no authors at all, missing numbers, LIKE wildcards in names, a podcast, a date,
# every `finished` state, a duplicated author, and empty-string names and tags.
EXTRA = [
    Book(source_id="X1", title="zebra crossing", format="Audiobook", authors=["Łukasz Żółć"],
         narrators=["ÉMILIE Dupont"], duration_minutes=300, my_rating=4.5,
         series=[SeriesEntry(name="Straße Saga", position=1)], tags=["Épique"]),
    Book(source_id="X2", title="Apple", format=None, authors=[], narrators=[]),
    Book(source_id="X3", title="Zed", format="Audiobook", authors=["100% Real_Name"],
         parent_category="Fantasy", child_category=None, duration_minutes=0, my_rating=0.0),
    Book(source_id="X4", title="A Podcast", format="Podcast", authors=["Łukasz Żółć"]),
    Book(source_id="X5", title="Edge Cases", format="Audiobook", authors=["Dup Author", "Dup Author", ""],
         narrators=[""], tags=["", "Grimdark"], child_category="Epic Fantasy", finished=True,
         release_date=date(2020, 2, 29), duration_minutes=1, my_rating=3.5,
         series=[SeriesEntry(name="Omnibus", position=1, position_end=3)]),
    Book(source_id="X6", title="Half Done", format="Audiobook", finished=False, minutes_left=10),
]
