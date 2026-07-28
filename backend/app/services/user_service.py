"""User registration / login / lookup.

FastAPI-agnostic: each function takes a SQLAlchemy ``Session`` and returns plain
ORM objects or raises :class:`BizError`. The router layer is responsible for
translating these into HTTP responses.
"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import SaUser


def register(db: Session, username: str, password: str) -> SaUser:
    """Create a new user. Raises ``BizError(1001)`` if the username is taken."""
    exists = db.execute(
        select(SaUser).where(SaUser.username == username)
    ).scalar_one_or_none()
    if exists is not None:
        raise BizError(1001, "用户名已存在")
    user = SaUser(username=username, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def login(db: Session, username: str, password: str) -> tuple[SaUser, str]:
    """Verify credentials and return ``(user, jwt_access_token)``.

    Raises ``BizError(1002)`` on either unknown username OR wrong password — the
    identical message avoids leaking which one was wrong.
    """
    user = db.execute(
        select(SaUser).where(SaUser.username == username)
    ).scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        raise BizError(1002, "用户名或密码错误")
    token = create_access_token(user.id)
    return user, token


def get_by_id(db: Session, user_id: int) -> Optional[SaUser]:
    """Return the user with ``id == user_id`` or None if no such row."""
    return db.execute(
        select(SaUser).where(SaUser.id == user_id)
    ).scalar_one_or_none()
