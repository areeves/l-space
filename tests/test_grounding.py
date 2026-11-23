import json

from l_space.ask.grounding import ungrounded_terms

ROWS = [
    {"title": "The Ember Crown", "authors": "Ilsa Verrow", "narrators": "Corin Ashdale", "length": "14h 20m"},
    {"title": "The Cinder Throne", "authors": "Ilsa Verrow", "narrators": "Corin Ashdale", "length": "15h 5m"},
]
QUESTION = "What has Corin Ashdale narrated?"
EVIDENCE = f"{QUESTION}\n" + json.dumps({"total_matching": 2, "shown": 2, "rows": ROWS})


def check(text, n_rows=2):
    return ungrounded_terms(text, EVIDENCE, n_rows)


def test_faithful_answer_passes():
    assert check("Corin Ashdale narrated The Ember Crown (14h 20m) and The Cinder Throne.") == []


def test_possessive_and_sentence_starters_are_fine():
    assert check("You have 2 books. Ilsa Verrow's The Ember Crown runs about 14 hours.") == []


def test_invented_title_is_flagged():
    assert "Dragonfall" in check("Corin Ashdale narrated Dragonfall and The Ember Crown.")


def test_invented_person_is_flagged():
    assert "Petra" in check("The Ember Crown was narrated by Petra Lindqvist.")


def test_invented_number_is_flagged():
    assert "47" in check("The Ember Crown is 47 hours long.")


def test_counts_up_to_row_count_allowed_for_enumeration():
    assert check("1. The Ember Crown\n2. The Cinder Throne") == []


def test_sentence_initial_invented_name_is_flagged():
    assert "Dragonfall" in check("Dragonfall is also on the list.")


def test_lowercase_words_are_not_checked():
    assert check("Corin Ashdale narrated both of these epic fantasy audiobooks.") == []


def test_equivalent_number_formats_match():
    # Seen in a live run: the rows said 5.0, the model wrote "5", and the check flagged it.
    evidence = json.dumps({"rows": [{"title": "The Ember Crown", "my_rating": 5.0}]})
    assert ungrounded_terms("You rated The Ember Crown 5 stars.", evidence, 1) == []
    assert ungrounded_terms("You rated The Ember Crown 5.0 stars.", evidence, 1) == []
    assert ungrounded_terms("You rated The Ember Crown 4 stars.", evidence, 1) == ["4"]


def test_digits_match_number_words_from_the_question():
    # Seen in a live run: the question said "five stars" and the model answered "5 stars".
    evidence = "Which narrators have I rated five stars?\n" + json.dumps({"rows": [{"books": 1}]})
    assert ungrounded_terms("These are the narrators you rated 5 stars.", evidence, 1) == []
    assert ungrounded_terms("These are the narrators you rated 4 stars.", evidence, 1) == ["4"]


def test_number_normalization_does_not_merge_different_numbers():
    evidence = json.dumps({"rows": [{"length": "10h 5m"}], "total_matching": 1})
    assert ungrounded_terms("It runs 10 hours.", evidence, 1) == []
    assert ungrounded_terms("It runs 100 hours.", evidence, 1) == ["100"]
    assert ungrounded_terms("It runs 1 hour and 50 minutes.", evidence, 1) == ["50"]
