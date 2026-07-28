"""Password hashing + JWT issue/verify.

This is the single home for cryptographic primitives: bcrypt-based password
hashing (via passlib) and HS256 JWT access tokens (via python-jose). Everything
else in the codebase talks to these helpers — never to the underlying libs
directly — so the crypto scheme can be swapped in one place.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of ``plain`` (use this for registration only)."""
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True iff ``plain`` matches the stored bcrypt ``hashed`` value."""
    return _pwd_ctx.verify(plain, hashed)


def create_access_token(subject: str | int) -> str:
    """Issue a signed JWT carrying ``sub=subject`` and the configured expiry.

    ``subject`` is the user id (int or str); it is normalised to ``str`` in the
    payload so the consumer can always ``int(payload["sub"])`` it back.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(subject),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_exp_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def decode_token(token: str) -> Optional[dict]:
    """Return the JWT payload dict, or None if the token is invalid/expired.

    Returning ``None`` (rather than raising) lets the dependency layer render a
    clean 401 without a try/except at every call site.
    """
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_alg])
    except JWTError:
        return None
