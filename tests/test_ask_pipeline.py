import httpx

from fakes import FakeProvider
from l_space.ask.answer import answer_question

LIST_JOSS = '{"op": "list_books", "narrator": "Joss Harlan"}'


def test_grounded_model_text_is_used(books):
    text = "Joss Harlan narrated A Lantern for the Drowned, Thornwood Accord and Thornwood Betrayal."
    provider = FakeProvider(LIST_JOSS, text)
    answer = answer_question("What has Joss Harlan narrated?", books, provider)
    assert answer.text == text
    assert (answer.grounded, answer.used_fallback, answer.abstained) == (True, False, False)
    assert answer.total == 3
    assert set(answer.timings_ms) == {"route", "write"}


def test_invented_title_falls_back_to_deterministic_text(books):
    provider = FakeProvider(LIST_JOSS, "Joss Harlan narrated Dragonfall and Thornwood Accord.")
    answer = answer_question("What has Joss Harlan narrated?", books, provider)
    assert answer.grounded is False
    assert answer.used_fallback is True
    assert "Dragonfall" in answer.ungrounded_terms
    assert "Dragonfall" not in answer.text
    assert "Thornwood Accord" in answer.text


def test_unsupported_abstains_without_calling_the_writer(books):
    provider = FakeProvider('{"op": "unsupported", "reason": "No plot data."}')
    answer = answer_question("Summarize the plot of The Cinder Throne.", books, provider)
    assert answer.abstained is True
    assert provider.write_calls == []
    assert "No plot data." in answer.text
    assert "I can list or count books" in answer.text


def test_empty_result_is_answered_without_the_writer(books):
    provider = FakeProvider('{"op": "list_books", "author": "Ivy Thornfield"}', "Dragonfall!")
    answer = answer_question("List the books by Ivy Thornfield.", books, provider)
    assert answer.text == "Nothing in your library matches that."
    assert answer.total == 0
    assert answer.abstained is False
    assert provider.write_calls == []


def test_invalid_plan_is_retried_once(books):
    provider = FakeProvider(["not json", '{"op": "in_progress"}'], "Thornwood Betrayal.")
    answer = answer_question("What am I partway through?", books, provider)
    assert answer.plan.op == "in_progress"
    assert answer.router_error is None
    assert len(provider.plan_calls) == 2
    assert "previous reply was invalid" in provider.plan_calls[1]


def test_two_invalid_plans_abstain(books):
    provider = FakeProvider(["nope", '{"op": "drop_table"}'])
    answer = answer_question("???", books, provider)
    assert answer.abstained is True
    assert answer.router_error is not None
    assert provider.write_calls == []


def test_writer_outage_falls_back_but_still_answers(books):
    class WriterDown(FakeProvider):
        def generate(self, prompt, system=None, schema=None, max_tokens=None):
            if schema is None:
                raise httpx.ConnectError("down")
            return super().generate(prompt, system, schema, max_tokens)

    answer = answer_question("How many by Maren Oakley?", books, WriterDown('{"op": "count_books", "author": "Maren Oakley"}'))
    assert answer.text == "4 books match."
    assert answer.used_fallback is True
    assert answer.grounded is None


def test_count_answers_can_use_the_total(books):
    provider = FakeProvider('{"op": "count_books", "author": "Maren Oakley"}', "You own 4 books by Maren Oakley.")
    answer = answer_question("How many books do I own by Maren Oakley?", books, provider)
    assert answer.grounded is True
    assert answer.text == "You own 4 books by Maren Oakley."


def test_plan_with_a_reason_is_treated_as_unsupported(books):
    # Seen in a live run: the model wrote "no wishlist information" but picked list_books anyway.
    plan = '{"op": "list_books", "reason": "The library has no wishlist information."}'
    provider = FakeProvider(plan, "should never be used")
    answer = answer_question("Which of my books are on my wishlist?", books, provider)
    assert answer.plan.op == "unsupported"
    assert answer.abstained is True
    assert "no wishlist information" in answer.text
    assert provider.write_calls == []


def test_a_plan_without_a_reason_is_left_alone(books):
    provider = FakeProvider('{"op": "list_books", "reason": null, "author": "Ilsa Verrow"}', "x")
    assert answer_question("List Ilsa Verrow.", books, provider).plan.op == "list_books"


def test_model_calls_are_length_capped(books):
    provider = FakeProvider(LIST_JOSS, "Joss Harlan narrated Thornwood Accord.")
    answer_question("What has Joss Harlan narrated?", books, provider)
    assert len(provider.max_tokens) == 2  # one routing call, one writing call
    assert all(cap is not None and cap > 0 for cap in provider.max_tokens)


def test_router_model_routes_and_writer_model_writes(books):
    router = FakeProvider(LIST_JOSS, "the router must never write")
    writer = FakeProvider("{}", "Joss Harlan narrated Thornwood Accord.")
    answer = answer_question("What has Joss Harlan narrated?", books, writer, router)
    assert len(router.plan_calls) == 1
    assert router.write_calls == []
    assert writer.plan_calls == []
    assert len(writer.write_calls) == 1
    assert answer.grounded is True
