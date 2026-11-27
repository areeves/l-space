"""Schema migrations: a fresh database, a database from an earlier release, and drift."""

import sqlite3
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

from awkward import EXTRA
from l_space.core.settings import Settings
from l_space.ingestion.loaders import load_ale_csv
from l_space.library import migrate as migrate_module
from l_space.library.engine import create_db_engine
from l_space.library.migrate import (
    BASELINE,
    current_revision,
    head_revision,
    migrate,
    needs_migration,
    prepare_database,
)
from l_space.library.store_sql import SqlLibraryStore, insert_books
from l_space.library.tables import metadata
from test_store_schema import assert_index_matches_blob

SAMPLE = Path(__file__).parent.parent / "data" / "sample" / "library.csv"
BOOKS = [*load_ale_csv(SAMPLE), *EXTRA]


def schema(engine) -> list[tuple]:
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
        )
        return [(name, " ".join(sql.split())) for name, sql in rows]


@pytest.fixture
def engine(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 't.db'}")
    yield engine
    engine.dispose()


def test_a_fresh_database_is_created_at_head(engine):
    assert needs_migration(engine)
    migrate(engine)
    assert current_revision(engine) == head_revision()
    assert not needs_migration(engine)


def test_migrating_is_idempotent(engine):
    migrate(engine)
    store = SqlLibraryStore(engine)
    store.import_books(BOOKS)
    migrate(engine)
    assert len(store.all_books()) == len({b.source_id for b in BOOKS})


def test_head_matches_the_table_definitions(engine):
    # No drift: a model change without a migration fails here.
    migrate(engine)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), metadata)
    assert diff == []


def test_head_creates_the_same_schema_as_the_metadata(engine):
    migrate(engine)
    reference = create_engine("sqlite://")
    metadata.create_all(reference)
    ours = [row for row in schema(engine) if row[0] != "alembic_version"]
    assert ours == schema(reference)


def test_the_baseline_is_the_first_revision():
    assert BASELINE == "0001"
    assert head_revision() >= BASELINE


# --- A database written before Alembic -----------------------------------------------------

# The schema as the previous release created it: hand-written SQL, unnamed constraints.
V2_DDL = (
    """CREATE TABLE books (
        id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL, data TEXT NOT NULL, is_audiobook INTEGER NOT NULL, format TEXT,
        duration_minutes INTEGER, my_rating REAL, finished INTEGER, release_date TEXT,
        parent_category_cf TEXT NOT NULL, child_category_cf TEXT NOT NULL)""",
    "CREATE INDEX idx_books_duration ON books (duration_minutes)",
    "CREATE INDEX idx_books_my_rating ON books (my_rating)",
    """CREATE TABLE book_people (
        book_id INTEGER NOT NULL REFERENCES books (id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK (role IN ('author', 'narrator')),
        name TEXT NOT NULL, name_cf TEXT NOT NULL)""",
    "CREATE INDEX idx_book_people_book ON book_people (book_id, role)",
    """CREATE TABLE book_series (
        book_id INTEGER NOT NULL REFERENCES books (id) ON DELETE CASCADE,
        name TEXT NOT NULL, name_cf TEXT NOT NULL, position REAL, position_end REAL)""",
    "CREATE INDEX idx_book_series_book ON book_series (book_id)",
    """CREATE TABLE book_tags (
        book_id INTEGER NOT NULL REFERENCES books (id) ON DELETE CASCADE,
        tag_cf TEXT NOT NULL)""",
    "CREATE INDEX idx_book_tags_book ON book_tags (book_id)",
)

# The very first release: just the blob, no derived columns.
V0_DDL = (
    """CREATE TABLE books (
        id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL, data TEXT NOT NULL)""",
)


def make_old_db(path: Path, ddl: tuple[str, ...], user_version: int) -> list[tuple]:
    """Create a database as an earlier release did; return its (id, data) rows."""
    conn = sqlite3.connect(path)
    for statement in ddl:
        conn.execute(statement)
    conn.execute(f"PRAGMA user_version = {user_version}")
    conn.commit()
    conn.close()
    engine = create_db_engine(f"sqlite:///{path}")
    if ddl is V2_DDL:
        with engine.begin() as conn:
            insert_books(conn, [(None, b) for b in BOOKS])
    else:
        with engine.begin() as conn:
            for b in BOOKS:
                conn.exec_driver_sql(
                    "INSERT INTO books (source_id, title, data) VALUES (?, ?, ?)",
                    (b.source_id, b.title, b.model_dump_json()),
                )
    with engine.begin() as conn:  # gaps in the ids: they must survive adoption
        conn.exec_driver_sql("DELETE FROM books WHERE id IN (3, 5)")
    with engine.connect() as conn:
        rows = [tuple(r) for r in conn.exec_driver_sql("SELECT id, data FROM books ORDER BY id")]
    engine.dispose()
    return rows


@pytest.mark.parametrize(("ddl", "version"), [(V2_DDL, 2), (V0_DDL, 0)], ids=["v2", "v0"])
def test_an_earlier_release_database_is_adopted_without_reimport(tmp_path, ddl, version):
    path = tmp_path / "old.db"
    before = make_old_db(path, ddl, version)
    engine = create_db_engine(f"sqlite:///{path}")

    migrate(engine)

    with engine.connect() as conn:
        after = [tuple(r) for r in conn.exec_driver_sql("SELECT id, data FROM books ORDER BY id")]
    assert after == before  # same ids, same blobs
    assert current_revision(engine) == head_revision()
    store = SqlLibraryStore(engine)
    assert assert_index_matches_blob(store) == len(BOOKS) - 2
    # Exactly the schema of a fresh database, named constraints included.
    fresh = create_db_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    migrate(fresh)
    assert schema(engine) == schema(fresh)
    engine.dispose()
    fresh.dispose()


def test_adoption_is_atomic(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    make_old_db(path, V0_DDL, 0)

    def boom(conn, rows):
        raise RuntimeError("interrupted")

    monkeypatch.setattr(migrate_module, "insert_books", boom)
    engine = create_db_engine(f"sqlite:///{path}")
    with pytest.raises(RuntimeError):
        migrate(engine)
    monkeypatch.undo()

    # Nothing changed, DDL included: still the old tables, not yet versioned.
    raw = sqlite3.connect(path)
    assert [r[1] for r in raw.execute("PRAGMA table_info(books)")] == [
        "id", "source_id", "title", "data",
    ]
    assert raw.execute("SELECT COUNT(*) FROM books").fetchone()[0] == len(BOOKS) - 2
    assert raw.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name = 'alembic_version'"
    ).fetchone()[0] == 0
    raw.close()
    migrate(engine)  # and the next attempt succeeds
    assert len(SqlLibraryStore(engine).all_books()) == len(BOOKS) - 2
    engine.dispose()


# --- Startup ---------------------------------------------------------------------------------


def test_auto_migrate_brings_a_new_database_up_to_date(tmp_path):
    settings = Settings(_env_file=None, db_path=tmp_path / "t.db")
    assert settings.auto_migrate  # the default
    prepare_database(settings)
    engine = create_db_engine(settings.sqlalchemy_url())
    assert not needs_migration(engine)
    engine.dispose()


def test_without_auto_migrate_an_out_of_date_database_is_refused(tmp_path):
    settings = Settings(_env_file=None, db_path=tmp_path / "t.db", auto_migrate=False)
    with pytest.raises(RuntimeError, match="l-space migrate"):
        prepare_database(settings)
    migrate(create_db_engine(settings.sqlalchemy_url()))  # the deploy step
    prepare_database(settings)  # now it starts


@pytest.fixture(autouse=True)
def fresh_engine_cache():
    from l_space.library import engine as engine_module

    engine_module._shared_engine.cache_clear()
    yield
    engine_module._shared_engine.cache_clear()


def test_the_cli_migrate_command(tmp_path, monkeypatch, capsys):
    from l_space import cli

    monkeypatch.setenv("L_SPACE_DB_PATH", str(tmp_path / "cli.db"))
    monkeypatch.setattr("sys.argv", ["l-space", "migrate"])
    cli.main()
    assert "up to date" in capsys.readouterr().out
    engine = create_db_engine(f"sqlite:///{tmp_path / 'cli.db'}")
    assert not needs_migration(engine)
    engine.dispose()
