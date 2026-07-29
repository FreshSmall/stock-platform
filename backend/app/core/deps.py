"""FastAPI dependency providers.

``get_db`` yields a SQLAlchemy session per request. ``get_current_user_id``
resolves the caller from the ``Authorization: Bearer <jwt>`` header by decoding
and validating the token (Task F1).
"""

from typing import Iterator

from fastapi import Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.security import decode_token


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

    Raises 401 on a missing header, malformed scheme, invalid/expired token, or
    a non-integer subject claim.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing token",
        )
    token = authorization.removeprefix("Bearer ").strip()
    payload = decode_token(token)
    if payload is None or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    try:
        return int(payload["sub"])
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token subject",
        )
