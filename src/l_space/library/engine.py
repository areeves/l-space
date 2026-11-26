"""Database engine factory (MULTI_USER_TODO.md, phase 0.5, decision D8).

SQLite runs behind a pool of one connection, so database access is serialized. That is plenty:
each call takes milliseconds and the model is the real bottleneck. It means the rule is
**connection scope = one store call or one transaction, never a whole request**: a request that
held the only connection across the ``/ask`` queue wait or the model call would block everything,
including login and ``/health``.

Every new SQLite connection turns foreign keys on (cascades depend on it) and sets a busy
timeout, because the CLI and ``migrate`` are separate processes on the same file. WAL and
``BEGIN IMMEDIATE`` are deliberately left out until there is real concurrency.

Transactions are explicit: the engine emits ``BEGIN`` itself when SQLAlchemy begins one. The
driver's default only starts a transaction before INSERT/UPDATE/DELETE, so DDL would run outside
any transaction and a failed schema rebuild could not be rolled back.
"""

from functools import cache
from pathlib import Path

from sqlalchemy import URL, Engine, create_engine, event, make_url
from sqlalchemy.pool import QueuePool, StaticPool

from l_space.core.settings import Settings

BUSY_TIMEOUT_MS = 5000
DEFAULT_POOL_TIMEOUT = 30.0


def create_db_engine(
    url: str | URL,
    *,
    pool_timeout: float = DEFAULT_POOL_TIMEOUT,
    busy_timeout_ms: int = BUSY_TIMEOUT_MS,
) -> Engine:
    """Create the engine for ``url``. Other databases get SQLAlchemy's defaults for now."""
    url = make_url(url)
    if url.get_backend_name() != "sqlite":
        return create_engine(url)
    return _create_sqlite_engine(url, pool_timeout, busy_timeout_ms)


def _is_memory(url: URL) -> bool:
    return url.database in (None, "", ":memory:")


def _create_sqlite_engine(url: URL, pool_timeout: float, busy_timeout_ms: int) -> Engine:
    # Connections are created in one thread and used from the request threadpool, which the
    # pool serializes, so the driver's same-thread check is switched off.
    connect_args = {"check_same_thread": False}
    if _is_memory(url):
        # One shared connection, so every checkout sees the same in-memory database (tests).
        engine = create_engine(url, poolclass=StaticPool, connect_args=connect_args)
    else:
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            url,
            poolclass=QueuePool,
            pool_size=1,
            max_overflow=0,
            pool_timeout=pool_timeout,
            connect_args=connect_args,
        )

    @event.listens_for(engine, "connect")
    def configure(dbapi_connection, _record) -> None:
        # Take transaction control away from the driver (SQLAlchemy's documented recipe for
        # pysqlite: "Serializable isolation / Savepoints / Transactional DDL").
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        cursor.close()

    @event.listens_for(engine, "begin")
    def begin(conn) -> None:
        conn.exec_driver_sql("BEGIN")

    return engine


@cache
def _shared_engine(url: URL, pool_timeout: float) -> Engine:
    return create_db_engine(url, pool_timeout=pool_timeout)


def get_engine(settings: Settings) -> Engine:
    """The process-wide engine for these settings: one engine, so one pool, per database URL.

    SQLite's pool of one only serializes access if everyone shares it (decision D8).
    """
    return _shared_engine(settings.sqlalchemy_url(), settings.db_pool_timeout)
