"""Alembic environment.

Normally the migration runs on a connection handed in by ``l_space.library.migrate`` (so it
shares the app's engine and sits inside one transaction). Run directly (``alembic revision
--autogenerate``, ``alembic upgrade head``) it builds the engine from ``Settings`` instead.
"""

from alembic import context

from l_space.library.tables import metadata


def run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=metadata,
        render_as_batch=True,  # SQLite cannot ALTER most things in place
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if context.is_offline_mode():
    raise RuntimeError("Offline (SQL script) migrations are not supported")
if connection is not None:
    run_migrations(connection)
else:
    from l_space.core.settings import get_settings
    from l_space.library.engine import get_engine

    with get_engine(get_settings()).begin() as connection:
        run_migrations(connection)
