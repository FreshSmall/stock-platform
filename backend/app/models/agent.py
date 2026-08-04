"""ORM for the V2 agent-report table (BP-V2-009~012).

4 agents (sector/market/review/recommend) write structured reports here.
"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.mysql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SaAgentReport(Base):
    """One report produced by a V2 agent (板块/大盘/复盘/推荐)."""

    __tablename__ = "sa_agent_report"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent: Mapped[str] = mapped_column(String(20), nullable=False, comment="sector/market/review/recommend")
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(200))
    target: Mapped[Optional[str]] = mapped_column(String(50), comment="板块/股票代码/留空(大盘)")
    summary: Mapped[Optional[str]] = mapped_column(Text)
    content: Mapped[Optional[str]] = mapped_column(Text, comment="完整报告(markdown)")
    scores: Mapped[Optional[dict]] = mapped_column(JSON, comment="评分维度")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"SaAgentReport(id={self.id!r}, agent={self.agent!r}, "
            f"trade_date={self.trade_date!r})"
        )
