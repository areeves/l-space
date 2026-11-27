"""How the store uses the engine (decision D8): short connection scope, one shared engine."""

import threading
from pathlib import Path

import pytest

from awkward import EXTRA
from l_space.core.settings import Settings
from l_space.ingestion.loaders import load_ale_csv
from l_space.library import engine as engine_module
from l_space.library.engine import get_engine
from l_space.library.filters import BookFilter
from l_space.library.migrate import migrate
from l_space.library.store import LibraryStore
from l_space.library.store_sql import SqlLibraryStore
from stores import new_store

SAMPLE = Path(__file__).parent.parent / "data" / "sample" / "library.csv"
BOOKS = [*load_ale_csv(SAMPLE), *EXTRA]


@pytest.fixture(autouse=True)
def fresh_engine_cache():
    engine_module._shared_engine.cache_clear()
    yield
    engine_module._shared_engine.cache_clear()


def test_the_store_holds_no_connection_between_calls(tmp_path):
    # With a pool of one, a call that kept its connection would block every other request.
    store = new_store(tmp_path / "t.db")
    pool = store.engine.pool
    calls = [
        lambda: store.replace_all(BOOKS),
        store.all_books,
        store.count_books,
        lambda: store.filter_books(BookFilter(author="verrow")),
    ]
    for call in calls:
        call()
        assert pool.checkedout() == 0
    store.engine.dispose()


def test_concurrent_store_calls_are_serialized_not_broken(tmp_path):
    store = new_store(tmp_path / "t.db")
    store.replace_all(BOOKS)
    errors = []

    def work() -> None:
        try:
            for _ in range(15):
                assert store.count_books() > 0
                store.filter_books(BookFilter(author="a"))
                store.all_books()
        except Exception as exc:  # noqa: BLE001 - a thread cannot raise into the test
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    store.engine.dispose()


def test_get_engine_shares_one_engine_per_database_url(tmp_path):
    a = Settings(_env_file=None, db_path=tmp_path / "a.db")
    same_as_a = Settings(_env_file=None, db_path=tmp_path / "a.db")
    b = Settings(_env_file=None, db_path=tmp_path / "b.db")

    assert get_engine(a) is get_engine(same_as_a)
    assert get_engine(a) is not get_engine(b)

    get_engine(a).dispose()
    get_engine(b).dispose()


def test_from_settings_builds_a_sql_store_on_the_shared_engine(tmp_path):
    settings = Settings(_env_file=None, db_path=tmp_path / "t.db")
    migrate(get_engine(settings))

    first = LibraryStore.from_settings(settings)
    assert isinstance(first, SqlLibraryStore)
    first.replace_all(BOOKS)

    second = LibraryStore.from_settings(settings)  # the next request builds a new store...
    assert second.engine is first.engine  # ...on the same engine
    assert len(second.all_books()) == len({b.source_id for b in BOOKS})

    first.engine.dispose()
