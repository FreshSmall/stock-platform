"""FastAPI dependency providers.

``get_db`` yields a SQLAlchemy session per request. ``get_current_user_id``
resolves the caller from the ``Authorization: Bearer <jwt>`` header by decoding
and validating the token (Task F1). ``require_admin_user`` (V1.5) additionally
loads the ``SaUser`` row and enforces that it is an enabled admin — used to
guard the ``/admin/*`` routes.
"""

from typing import Iterator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.security import decode_token
from app.models.user import SaUser


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


def require_admin_user(
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> SaUser:
    """Resolve the caller and enforce an enabled admin account (V1.5).

    Composes ``get_current_user_id`` (token → id) with a DB lookup of the
    ``SaUser`` row. Raises 401 if the user no longer exists, 403 if the user is
    not an admin or has been disabled (``status != 1``). Returns the full
    ``SaUser`` so admin handlers can read ``username`` etc. without a second
    query.
    """
    user = db.scalar(select(SaUser).where(SaUser.id == user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    if user.status != 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account disabled",
        )
    return user

