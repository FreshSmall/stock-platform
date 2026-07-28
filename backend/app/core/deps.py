"""FastAPI dependency providers.

``get_db`` yields a SQLAlchemy session per request. ``get_current_user_id``
resolves the caller from the ``Authorization`` header.

NOTE: ``get_current_user_id`` is a **placeholder**. Real JWT decoding lands in
Task F1 (``app/core/security.py``). Until then any non-empty bearer token is
accepted and user_id is hard-coded to ``1``.
"""

from typing import Iterator

from fastapi import Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import SessionLocal


def get_db() -> Iterator[Session]:
    """Yield a request-scoped SQLAlchemy session, closing it when done."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def get_current_user_id(
    authorization: str | None = Header(default=None),
) -> int:
    """Resolve the current user id from the ``Authorization: Bearer <token>`` header.

    TODO(F1): replace this placeholder body with a real JWT decode, e.g.::

        from app.core.security import decode_token
        payload = decode_token(token)
        return int(payload["sub"])

    Until F1 wires ``app/core/security.py`` up, any non-empty bearer token is
    accepted and user_id is hard-coded to ``1``.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing token",
        )
    # token = authorization.removeprefix("Bearer ").strip()  # used in F1
    return 1
