"""ORM mappings for the V1.5 market-sentiment ``sa_`` tables.

Contains:

* :class:`SaMarketSentiment` - market-wide daily sentiment rollup
  (limit-up/down counts, failed-limit count, seal rate, max streak, ladder).
* :class:`SaLimitUpStreak`   - per-stock consecutive limit-up day counter,
  the input from which streaks and the limit-up ladder are derived.

Both are owned and written by this service; migrated by Alembic. The sentiment
row is computed from ``daily_prices`` (read-only) by the sentiment service.
"""

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Date,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SaMarketSentiment(Base):
    """Market-wide daily sentiment rollup for one trade day.

    Maps the ``sa_market_sentiment`` table. ``streak_ladder`` is a JSON object
    mapping streak-days to stock count, e.g. ``{"1": 32, "2": 8, "3": 1}``.
    """

    __tablename__ = "sa_market_sentiment"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    limit_up_count: Mapped[Optional[int]] = mapped_column(Integer)
    limit_down_count: Mapped[Optional[int]] = mapped_column(Integer)
    failed_limit_count: Mapped[Optional[int]] = mapped_column(Integer)
    seal_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    max_streak: Mapped[Optional[int]] = mapped_column(Integer)
    up_count: Mapped[Optional[int]] = mapped_column(Integer)
    down_count: Mapped[Optional[int]] = mapped_column(Integer)
    streak_ladder: Mapped[Optional[dict]] = mapped_column(JSON)

    __table_args__ = (
        UniqueConstraint("trade_date", name="uk_date"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaMarketSentiment(id={self.id!r}, trade_date={self.trade_date!r}, "
            f"limit_up_count={self.limit_up_count!r})"
        )


class SaLimitUpStreak(Base):
    """Per-stock consecutive limit-up day counter for one trade day.

    Maps the ``sa_limit_up_streak`` table. ``streak_days`` resets to 0 when the
    stock fails to hit the limit-up; otherwise it increments from the prior
    trading day. This is the raw input for the ladder and max-streak rollup.
    """

    __tablename__ = "sa_limit_up_streak"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    streak_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("stock_code", "trade_date", name="uk_code_date"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaLimitUpStreak(id={self.id!r}, stock_code={self.stock_code!r}, "
            f"trade_date={self.trade_date!r}, streak_days={self.streak_days!r})"
        )
