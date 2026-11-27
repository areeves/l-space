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
    from stores import new_store

    store = new_store()
    store.replace_all(books)
    return store


# Store back ends the shared store tests run against. Postgres joins this list in phase 5.
STORE_BACKENDS = ["sqlite-memory", "sqlite-file"]


@pytest.fixture(scope="module", params=STORE_BACKENDS)
def empty_store(request, tmp_path_factory):
    """An empty store with its schema, once per back end."""
    from stores import new_store

    path = ":memory:"
    if request.param == "sqlite-file":
        path = tmp_path_factory.mktemp("store") / "t.db"
    store = new_store(path)
    yield store
    store.engine.dispose()
