"""Auth router (Task F1).

Mounted under ``/api/v1/auth``. Three endpoints:

* ``POST /register`` - create a user (username + password).
* ``POST /login``    - verify credentials and return a JWT access token.
* ``GET  /me``       - the current caller's profile (requires a valid JWT).
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id, get_db
from app.services import user_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _ok(data=None, msg: str = "ok") -> dict:
    """Build the unified success envelope (lazy import to avoid a cycle)."""
    from app.main import api_ok

    return api_ok(data, msg)


class CredentialsIn(BaseModel):
    """Shared body for ``/register`` and ``/login``."""

    username: str
    password: str


@router.post("/register")
def register(payload: CredentialsIn, db: Session = Depends(get_db)) -> dict:
    """Register a new user. ``BizError(1001)`` if the username already exists."""
    user = user_service.register(db, payload.username, payload.password)
    return _ok({"id": user.id, "username": user.username})


@router.post("/login")
def login(payload: CredentialsIn, db: Session = Depends(get_db)) -> dict:
    """Verify credentials and return ``{token, user}``. ``BizError(1002)`` on miss."""
    user, token = user_service.login(db, payload.username, payload.password)
    return _ok({"token": token, "user": {"id": user.id, "username": user.username}})


@router.get("/me")
def me(
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """Return the caller's profile. 401 if the JWT is missing or invalid."""
    user = user_service.get_by_id(db, user_id)
    if user is None:
        return _ok(None, msg="user not found")
    return _ok({"id": user.id, "username": user.username})
