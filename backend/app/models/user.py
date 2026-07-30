"""ORM mapping for the ``sa_user`` table (application-managed users).

Unlike :mod:`app.models.stock`, every model whose table name starts with
``sa_`` is owned by this service: we create it, migrate it and write to it.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SaUser(Base):
    """An application user who can log in, request analyses, run backtests, etc.

    Maps the ``sa_user`` table. ``password_hash`` stores a hashed passphrase
    (never plaintext). ``username`` is unique. ``role`` is ``user`` or ``admin``
    (V1.5); ``status`` is 1=enabled, 0=disabled (V1.5).
    """

    __tablename__ = "sa_user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False, default="user")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"SaUser(id={self.id!r}, username={self.username!r})"
