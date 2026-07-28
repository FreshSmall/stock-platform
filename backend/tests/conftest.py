"""Pytest fixtures for database integration tests.

These fixtures hit the REAL ``stock_analysis`` database (no mocks). The
connection details come from :class:`app.core.config.Settings`, which reads
``.env``. Both fixtures open a short-lived session and close it in ``finally``
so a failing assertion cannot leak a connection back to the pool.
"""

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, engine


@pytest.fixture
def db_engine() -> Engine:
    """Yield the shared SQLAlchemy engine bound to the live database."""
    yield engine


@pytest.fixture
def db_session() -> Session:
    """Yield a SQLAlchemy session connected to the real DB, closed after use."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
