"""Pytest fixtures for database integration tests.

These fixtures hit the REAL ``stock_analysis`` database (no mocks). The
connection details come from :class:`app.core.config.Settings`, which reads
``.env``. Both fixtures open a short-lived session and close it in ``finally``
so a failing assertion cannot leak a connection back to the pool.
"""

import uuid

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, engine
from app.models.user import SaUser


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


@pytest.fixture
def auth_token() -> str:
    """Register a unique user and yield a real JWT access token for it.

    Replaces the A4 placeholder behaviour (``Bearer test``). Each test that uses
    this fixture gets its own freshly-registered user, which also isolates the
    per-USER rate-limit buckets introduced in F2. The user row is deleted after
    the test.
    """
    from app.services import user_service

    username = f"tester_{uuid.uuid4().hex[:8]}"
    session = SessionLocal()
    try:
        user = user_service.register(session, username, "pw-test-123")
        user_id = user.id
    finally:
        session.close()

    # Issue the token against a fresh, dedicated session so the fixture never
    # hands a token tied to a closed/cleared session's identity map.
    session = SessionLocal()
    try:
        _, token = user_service.login(session, username, "pw-test-123")
    finally:
        session.close()

    yield token

    # cleanup: remove the user row so the suite stays idempotent.
    session = SessionLocal()
    try:
        session.query(SaUser).filter_by(id=user_id).delete()
        session.commit()
    finally:
        session.close()


@pytest.fixture
def auth_headers(auth_token: str) -> dict:
    """Convenience wrapper: the ``Authorization`` header dict for ``auth_token``."""
    return {"Authorization": f"Bearer {auth_token}"}

