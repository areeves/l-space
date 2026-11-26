"""The engine factory and the settings that feed it (decision D8)."""

import threading
from pathlib import Path

import pytest
from sqlalchemy import URL
from sqlalchemy.exc import IntegrityError, TimeoutError

from l_space.core.settings import Settings
from l_space.library.engine import BUSY_TIMEOUT_MS, create_db_engine


def file_url(path: Path) -> URL:
    return URL.create("sqlite", database=str(path))


@pytest.fixture(params=["sqlite://", "sqlite:///:memory:", "file"])
def engine(request, tmp_path):
    url = file_url(tmp_path / "t.db") if request.param == "file" else request.param
    engine = create_db_engine(url)
    yield engine
    engine.dispose()


def test_every_connection_enables_foreign_keys_and_a_busy_timeout(engine):
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == BUSY_TIMEOUT_MS


def test_the_busy_timeout_is_configurable(tmp_path):
    # A non-default value, because the sqlite3 driver already defaults to 5000 ms.
    engine = create_db_engine(file_url(tmp_path / "t.db"), busy_timeout_ms=1234)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 1234
    engine.dispose()


def test_foreign_keys_are_enforced(engine):
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql(
            "CREATE TABLE child (parent_id INTEGER REFERENCES parent (id) ON DELETE CASCADE)"
        )
        conn.exec_driver_sql("INSERT INTO parent VALUES (1)")
        conn.exec_driver_sql("INSERT INTO child VALUES (1)")
    with engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM parent")
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM child").scalar() == 0
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO child VALUES (99)")


def test_the_database_is_shared_between_checkouts(engine):
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE t (x INTEGER)")
        conn.exec_driver_sql("INSERT INTO t VALUES (7)")
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT x FROM t").scalar() == 7


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:"])
def test_an_in_memory_database_is_shared_across_threads(url):
    # The request threadpool checks connections out from different threads; each must see the
    # same in-memory database, not a fresh empty one.
    engine = create_db_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE t (x INTEGER)")
    seen = []

    def read() -> None:
        with engine.connect() as conn:
            seen.append(conn.exec_driver_sql("SELECT COUNT(*) FROM t").scalar())

    worker = threading.Thread(target=read)
    worker.start()
    worker.join()
    assert seen == [0]
    engine.dispose()


def test_a_connection_can_be_used_from_another_thread(engine):
    seen = []
    with engine.connect() as conn:
        worker = threading.Thread(
            target=lambda: seen.append(conn.exec_driver_sql("SELECT 1").scalar())
        )
        worker.start()
        worker.join()
    assert seen == [1]


def test_file_databases_use_a_pool_of_one(tmp_path):
    engine = create_db_engine(file_url(tmp_path / "t.db"), pool_timeout=0.2)
    # The only connection is checked out, so a second request waits, then times out.
    with engine.connect(), pytest.raises(TimeoutError):
        engine.connect()
    with engine.connect() as conn:  # released, so the next request gets it
        assert conn.exec_driver_sql("SELECT 1").scalar() == 1
    engine.dispose()


def test_the_parent_directory_is_created(tmp_path):
    path = tmp_path / "nested" / "dir" / "t.db"
    engine = create_db_engine(file_url(path))
    with engine.connect():
        pass
    engine.dispose()
    assert path.exists()


def test_settings_build_a_sqlite_url_from_db_path():
    settings = Settings(_env_file=None, db_path=Path("data/x.db"))
    url = settings.sqlalchemy_url()
    assert url.get_backend_name() == "sqlite"
    assert url.database == str(Path("data/x.db"))


def test_database_url_wins_over_db_path():
    settings = Settings(
        _env_file=None, db_path=Path("data/x.db"), database_url="sqlite:///other/y.db"
    )
    assert settings.sqlalchemy_url().database == "other/y.db"


def test_database_url_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("L_SPACE_DATABASE_URL", "sqlite:///from-env.db")
    monkeypatch.setenv("L_SPACE_DB_POOL_TIMEOUT", "2.5")
    settings = Settings(_env_file=None)
    assert settings.sqlalchemy_url().database == "from-env.db"
    assert settings.db_pool_timeout == 2.5


def test_the_default_pool_timeout():
    assert Settings(_env_file=None).db_pool_timeout == 30.0
