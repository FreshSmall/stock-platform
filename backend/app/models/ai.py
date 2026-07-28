"""ORM mappings for AI-analysis related tables.

Contains:

* :class:`SaAiAnalysis` - a persisted AI analysis result for one stock.
* :class:`SaAiChatSession` - one chat conversation belonging to a user.
* :class:`SaAiChatMessage` - a single message within a chat session.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SaAiAnalysis(Base):
    """A persisted AI analysis result for a single stock.

    ``request_id`` is the unique correlation id of the analysis request. The
    five ``score_*`` columns break the overall ``score`` down by dimension;
    the matching ``TEXT`` columns hold the structured/JSON payload per
    dimension and ``full_text`` stores the rendered Markdown report.
    """

    __tablename__ = "sa_ai_analysis"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    score_fundamental: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    score_technical: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    score_capital: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    score_news: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    score_risk: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    fundamentals: Mapped[Optional[str]] = mapped_column(Text)
    technicals: Mapped[Optional[str]] = mapped_column(Text)
    capital: Mapped[Optional[str]] = mapped_column(Text)
    news: Mapped[Optional[str]] = mapped_column(Text)
    risk: Mapped[Optional[str]] = mapped_column(Text)
    full_text: Mapped[Optional[str]] = mapped_column(MEDIUMTEXT)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_code_created", "stock_code", "created_at"),
        Index("idx_user", "user_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaAiAnalysis(id={self.id!r}, request_id={self.request_id!r}, "
            f"stock_code={self.stock_code!r}, score={self.score!r})"
        )


class SaAiChatSession(Base):
    """A single AI chat conversation owned by one user."""

    __tablename__ = "sa_ai_chat_session"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("idx_user", "user_id"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaAiChatSession(id={self.id!r}, session_id={self.session_id!r}, "
            f"user_id={self.user_id!r})"
        )


class SaAiChatMessage(Base):
    """A single message within a :class:`SaAiChatSession`."""

    __tablename__ = "sa_ai_chat_message"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    content: Mapped[str] = mapped_column(MEDIUMTEXT, nullable=False)
    tool_calls: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_session_created", "session_id", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaAiChatMessage(id={self.id!r}, session_id={self.session_id!r}, "
            f"role={self.role!r})"
        )
