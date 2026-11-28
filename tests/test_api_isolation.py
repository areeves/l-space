"""Every route acts only on the caller's own library (decisions D2 and D5)."""

from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from fakes import FakeProvider
from l_space.api.routes import current_user, get_library, get_provider, get_store, router
from l_space.api.schemas import BookOut
from l_space.library import queries
from l_space.library.models import Book
from l_space.main import create_app

SAMPLE = Path(__file__).parent.parent / "data" / "sample" / "library.csv"


def client_as(store, user, provider=None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[current_user] = lambda: user
    if provider is not None:
        app.dependency_overrides[get_provider] = lambda: provider
    return TestClient(app)


@pytest.fixture
def api(two_users):
    store, alice, bob = two_users
    users = {"alice": store.find_user("alice"), "bob": store.find_user("bob")}
    return store, {name: client_as(store, user) for name, user in users.items()}, alice, bob


def flatten(dependant) -> set:
    calls = {dependant.call}
    for sub in dependant.dependencies:
        calls |= flatten(sub)
    return calls


def test_every_route_but_health_reaches_books_only_through_get_library():
    # A new route that forgot to depend on the caller's library fails here, not in review.
    routes = [r for r in router.routes if isinstance(r, APIRoute)]
    assert {r.path for r in routes} >= {"/library/books", "/library/books/{public_id}", "/ask"}
    for route in routes:
        if route.path == "/health":
            continue
        calls = flatten(route.dependant)
        assert get_library in calls and current_user in calls, route.path


def test_the_caller_decides_which_books_are_listed(api):
    _, clients, *_ = api
    mine = clients["alice"].get("/library/books").json()
    theirs = clients["bob"].get("/library/books").json()
    assert [b["title"] for b in mine] == [
        "Alice's Shared 0", "Alice's Shared 1", "Alice's Shared 2", "Only Alice",
    ]
    assert len(theirs) == 5 and not {b["public_id"] for b in mine} & {b["public_id"] for b in theirs}


def test_listed_books_have_a_public_id_and_nothing_internal(api):
    _, clients, *_ = api
    for book in clients["alice"].get("/library/books").json():
        assert set(book) == set(BookOut.model_fields)
        assert "id" not in book and "raw" not in book
        assert book["public_id"] and book["source_id"]


def test_book_out_is_an_explicit_contract():
    # A field added to Book is not exposed until someone adds it to BookOut on purpose.
    assert set(Book.model_fields) - set(BookOut.model_fields) == {"id", "raw"}
    with pytest.raises(ValueError, match="public id"):
        BookOut.from_book(Book(source_id="S", title="T"))


def test_a_book_can_be_fetched_by_its_public_id(api):
    _, clients, *_ = api
    listed = clients["alice"].get("/library/books").json()[0]
    resp = clients["alice"].get(f"/library/books/{listed['public_id']}")
    assert resp.status_code == 200 and resp.json() == listed


def test_another_users_public_id_is_a_404_identical_to_an_unknown_one(api):
    _, clients, *_ = api
    theirs = clients["bob"].get("/library/books").json()[0]["public_id"]
    other = clients["alice"].get(f"/library/books/{theirs}")
    unknown = clients["alice"].get("/library/books/no-such-id")
    assert other.status_code == unknown.status_code == 404
    assert other.json() == unknown.json()  # nothing tells the two cases apart
    assert clients["bob"].get(f"/library/books/{theirs}").status_code == 200  # bob still can


def test_integer_ids_do_not_open_books(api):
    _, clients, *_ = api
    assert clients["alice"].get("/library/books/1").status_code == 404


def test_aggregate_routes_are_scoped(api):
    _, clients, alice, bob = api
    assert [tuple(r) for r in clients["alice"].get("/library/narrators").json()] == (
        queries.narrator_counts(alice.all_books())
    )
    assert [tuple(r) for r in clients["bob"].get("/library/narrators").json()] == (
        queries.narrator_counts(bob.all_books())
    )
    assert clients["alice"].get("/library/series-gaps").json() == queries.series_gaps(alice.all_books())
    assert "Bob Saga" not in str(clients["alice"].get("/library/series-gaps").json())


def test_an_import_changes_only_the_callers_library(api):
    _, clients, alice, bob = api
    bob_before = bob.all_books()
    resp = clients["alice"].post("/library/import", params={"path": str(SAMPLE)})
    assert resp.json()["added"] == 12 and resp.json()["removed"] == 4  # alice's own four are gone
    assert bob.all_books() == bob_before  # ids and public ids included
    assert len(alice.all_books()) == 12


def test_ask_answers_from_the_callers_library(two_users):
    store, alice, _bob = two_users
    plan = '{"op": "count_books"}'
    for name, expected in (("alice", 4), ("bob", 4)):
        client = client_as(store, store.find_user(name), FakeProvider(plan, "Some books."))
        body = client.post("/ask", json={"question": "How many books?"}).json()
        assert body["total"] == expected
    alice.import_books([])  # alice's library is now empty; bob's is not
    empty = client_as(store, store.find_user("alice"), FakeProvider(plan))
    assert empty.post("/ask", json={"question": "How many?"}).status_code == 409
    full = client_as(store, store.find_user("bob"), FakeProvider(plan, "Some books."))
    assert full.post("/ask", json={"question": "How many?"}).status_code == 200


def test_the_default_caller_is_the_default_owner(tmp_path):
    from l_space.core.settings import Settings
    from l_space.library.engine import get_engine
    from l_space.library.migrate import migrate
    from l_space.library.store import LibraryStore

    settings = Settings(_env_file=None, db_path=tmp_path / "t.db")
    migrate(get_engine(settings))
    app = create_app()
    app.dependency_overrides[get_store] = lambda: LibraryStore.from_settings(settings)
    client = TestClient(app)
    assert client.post("/library/import", params={"path": str(SAMPLE)}).json()["added"] == 12
    assert len(client.get("/library/books").json()) == 12
    get_engine(settings).dispose()
