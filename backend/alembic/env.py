"""Alembic environment.

Wires Alembic to the project's real engine (``app.core.database.engine``) and
to ``Base.metadata`` so that ``alembic revision --autogenerate`` diffs the
current database against the union of every ORM mapping.

Importing :mod:`app.models` is what makes the new ``sa_`` mappings visible to
``Base.metadata`` - without that import autogenerate would not see them.

We deliberately do NOT write the URL into ``alembic.ini`` (it is left blank)
nor into the alembic ``config`` object: configparser's default ``%``-style
interpolation chokes on the URL-encoded password (``%40`` for ``@``). Instead
we connect using the pre-built engine directly.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool

# Ensure the backend package root (the directory containing ``app/``) is on
# ``sys.path`` so ``import app.*`` works regardless of the cwd Alembic was
# invoked from.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.database import Base, engine  # noqa: E402  (after sys.path tweak)
import app.models  # noqa: F401,E402  (registers all mappings with Base.metadata)

# this is the Alembic Config object.
config = context.config

# Interpret the config file for Python logging, if present.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Emits SQL to stdout rather than connecting to the DB. Uses the URL
    configured on the real engine (already URL-encodes the password).
    """
    context.configure(
        url=engine.url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Uses the project's real engine (which carries the URL-encrypted password)
    with a ``NullPool`` so connections are not held between migration steps.
    """
    # Re-create a NullPool-bound engine pointing at the same URL so the
    # long-lived application pool isn't used by short-lived migration runs.
    from sqlalchemy import create_engine

    connectable = create_engine(
        engine.url.render_as_string(hide_password=False),
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
