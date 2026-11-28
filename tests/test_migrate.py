"""Schema migrations: a fresh database, a database from an earlier release, and drift."""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text

from awkward import EXTRA
from l_space.core.settings import Settings
from l_space.ingestion.loaders import load_ale_csv
from l_space.library import migrate as migrate_module
from l_space.library.engine import create_db_engine
from l_space.library.migrate import (
    BASELINE,
    alembic_config,
    current_revision,
    head_revision,
    migrate,
    needs_migration,
    prepare_database,
)
from l_space.library.models import Book
from l_space.library.store_sql import (
    SqlLibraryStore,
    _blob,
    _insert_children,
    _row_values,
    index_rows,
)
from l_space.library.tables import metadata
from test_store_schema import assert_index_matches_blob

SAMPLE = Path(__file__).parent.parent / "data" / "sample" / "library.csv"
BOOKS = [*load_ale_csv(SAMPLE), *EXTRA]


def schema(engine) -> list[tuple]:
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
        )
        # SQLite writes `CREATE TABLE "books"` after a rename and `books` otherwise: same table.
        return [(name, " ".join(sql.replace('"', "").split())) for name, sql in rows]


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


def library_of(engine):
    store = SqlLibraryStore(engine)
    return store.for_user(store.default_owner().id)


def test_migrating_is_idempotent(engine):
    migrate(engine)
    store = library_of(engine)
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


def insert_old_shape(conn, books) -> None:
    """Books written the way releases before ownership did: no ``user_id``, no ``public_id``."""
    for book in books:
        values = _row_values(book, _blob(book))
        columns = ", ".join(values)
        marks = ", ".join(f":{name}" for name in values)
        result = conn.execute(text(f"INSERT INTO books ({columns}) VALUES ({marks})"), values)
        _insert_children(conn, result.lastrowid, index_rows(book))


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
            insert_old_shape(conn, BOOKS)
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
    store = library_of(engine)
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

    def boom(*args, **kwargs):
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
    assert len(library_of(engine).all_books()) == len(BOOKS) - 2
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


# --- Ownership (migration 0002) --------------------------------------------------------------


def owners(engine) -> list[tuple]:
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT username, username_cf, password_hash, is_admin, disabled FROM users"
        )
        return [tuple(r) for r in rows]


def baseline_database(engine) -> None:
    """A database at revision 0001 holding the books, with gaps in the ids (3, 5 and the last)."""
    with engine.begin() as conn:
        command.upgrade(alembic_config(conn), BASELINE)
        insert_old_shape(conn, BOOKS)
        last = conn.exec_driver_sql("SELECT MAX(id) FROM books").scalar()
        conn.exec_driver_sql(f"DELETE FROM books WHERE id IN (3, 5, {last})")


def snapshot(engine) -> dict:
    with engine.connect() as conn:
        def rows(query):
            return sorted(tuple(r) for r in conn.exec_driver_sql(query))

        return {
            "books": rows(
                "SELECT id, source_id, title, data, is_audiobook, format, duration_minutes, "
                "my_rating, finished, release_date, parent_category_cf, child_category_cf FROM books"
            ),
            "people": rows("SELECT * FROM book_people"),
            "series": rows("SELECT * FROM book_series"),
            "tags": rows("SELECT * FROM book_tags"),
            "sequence": rows("SELECT seq FROM sqlite_sequence WHERE name = 'books'"),
        }


def test_a_fresh_database_gets_the_default_owner(engine):
    migrate(engine)
    assert owners(engine) == [("owner", "owner", None, 1, 0)]  # admin, cannot log in yet


def test_the_owner_can_be_named(engine):
    migrate(engine, owner="  Alice ")
    assert owners(engine) == [("Alice", "alice", None, 1, 0)]
    migrate(engine, owner="Someone Else")  # ignored once users exist
    assert owners(engine) == [("Alice", "alice", None, 1, 0)]


def test_a_blank_owner_name_is_refused_and_nothing_changes(engine):
    with pytest.raises(ValueError, match="needs a name"):
        migrate(engine, owner="   ")
    assert current_revision(engine) is None


def test_upgrading_a_populated_database_keeps_every_book_and_child_row(engine):
    baseline_database(engine)
    before = snapshot(engine)
    assert before["people"] and before["series"] and before["tags"]  # the trap needs children

    migrate(engine)

    after = snapshot(engine)
    assert after == before  # books, children and the autoincrement counter, untouched
    with engine.connect() as conn:
        rows = conn.exec_driver_sql("SELECT user_id, public_id FROM books").all()
        owner_id = conn.exec_driver_sql("SELECT id FROM users").scalar()
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1  # restored
    assert {user_id for user_id, _ in rows} == {owner_id}  # the owner got every book
    public_ids = [public_id for _, public_id in rows]
    assert len(set(public_ids)) == len(public_ids) == len(before["books"])
    assert all(len(p) >= 16 for p in public_ids)
    assert assert_index_matches_blob(library_of(engine)) == len(before["books"])


def test_the_upgraded_schema_is_the_fresh_one(engine, tmp_path):
    baseline_database(engine)
    migrate(engine)
    fresh = create_db_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    migrate(fresh)
    assert schema(engine) == schema(fresh)
    fresh.dispose()


def test_ids_are_not_reused_after_the_upgrade(engine):
    baseline_database(engine)
    seq_before = snapshot(engine)["sequence"][0][0]
    migrate(engine)
    store = library_of(engine)
    store.import_books([*store.all_books(), Book(source_id="NEW", title="New")])
    with engine.connect() as conn:
        new_id = conn.exec_driver_sql("SELECT id FROM books WHERE source_id = 'NEW'").scalar()
    assert new_id > seq_before


def test_the_default_owner_setting_and_the_cli_flag(tmp_path, monkeypatch):
    from l_space import cli
    from l_space.library import engine as engine_module

    settings = Settings(_env_file=None, db_path=tmp_path / "a.db", default_owner="Zed")
    prepare_database(settings)
    engine_a = create_db_engine(settings.sqlalchemy_url())
    assert owners(engine_a)[0][:2] == ("Zed", "zed")
    engine_a.dispose()

    engine_module._shared_engine.cache_clear()
    monkeypatch.setenv("L_SPACE_DB_PATH", str(tmp_path / "b.db"))
    monkeypatch.setenv("L_SPACE_DEFAULT_OWNER", "Ignored")
    monkeypatch.setattr("sys.argv", ["l-space", "migrate", "--owner", "Flagged"])
    cli.main()
    engine_b = create_db_engine(f"sqlite:///{tmp_path / 'b.db'}")
    assert owners(engine_b)[0][:2] == ("Flagged", "flagged")  # the flag beats the setting
    engine_b.dispose()
