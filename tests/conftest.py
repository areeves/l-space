from pathlib import Path

import pytest

from l_space.ingestion.loaders import load_ale_csv

SAMPLE = Path(__file__).parent.parent / "data" / "sample" / "library.csv"


@pytest.fixture(scope="session")
def books():
    return list(load_ale_csv(SAMPLE))


@pytest.fixture(scope="session")
def library(books):
    """A store holding the sample library (read-only: tests must not change it)."""
    from stores import new_library

    store = new_library()
    store.import_books(books)
    return store


# Store back ends the shared store tests run against. Postgres joins this list in phase 5.
STORE_BACKENDS = ["sqlite-memory", "sqlite-file"]


@pytest.fixture(scope="module", params=STORE_BACKENDS)
def empty_library(request, tmp_path_factory):
    """An empty library with its schema, once per back end.

    Another user's books are in the same database, with the same ASINs but different data, so
    any query that forgets to scope itself returns the wrong books and fails the tests.
    """
    from stores import new_store

    path = ":memory:"
    if request.param == "sqlite-file":
        path = tmp_path_factory.mktemp("store") / "t.db"
    store = new_store(path)
    noise = store.create_user("noise")
    store.for_user(noise.id).import_books(
        b.model_copy(update={"title": f"Noise {b.title}", "my_rating": 5.0, "finished": False})
        for b in NOISE_BOOKS()
    )
    yield store.for_user(store.default_owner().id)
    store.engine.dispose()


def NOISE_BOOKS():
    from awkward import EXTRA
    from l_space.ingestion.loaders import load_ale_csv

    return [*load_ale_csv(SAMPLE), *EXTRA]


@pytest.fixture
def two_users():
    """A store with alice and bob. They own the same ASINs with different data, plus books each
    that the other lacks. Returns (store, alice, bob) where alice and bob are their libraries."""
    from l_space.library.models import Book, SeriesEntry
    from stores import new_store

    store = new_store()
    alice_user, bob_user = store.create_user("alice"), store.create_user("bob")
    alice, bob = store.for_user(alice_user.id), store.for_user(bob_user.id)
    shared = [
        Book(source_id=f"SHARED{i}", title=f"Shared {i}", authors=["Common Author"],
             narrators=["Common Narrator"], finished=False, my_rating=3.0,
             series=[SeriesEntry(name="Shared Saga", position=i + 2)])
        for i in range(3)
    ]
    alice.import_books([
        *(b.model_copy(update={"title": f"Alice's {b.title}"}) for b in shared),
        Book(source_id="A-ONLY", title="Only Alice", authors=["Alice Author"],
             narrators=["Alice Narrator"], finished=False,
             series=[SeriesEntry(name="Alice Saga", position=2)]),
    ])
    bob.import_books([
        *(b.model_copy(update={"title": f"Bob's {b.title}", "authors": ["Bob Author"]}) for b in shared),
        Book(source_id="B-ONLY", title="Only Bob", authors=["Bob Author"],
             narrators=["Bob Narrator"], finished=False,
             series=[SeriesEntry(name="Bob Saga", position=2)]),
        Book(source_id="B-POD", title="Bob's Podcast", format="Podcast", narrators=["Bob Pod"]),
    ])
    yield store, alice, bob
    store.engine.dispose()
