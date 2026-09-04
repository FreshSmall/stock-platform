"""ORM mappings for V2.1 data-repair ``sa_`` tables.

The V2.1 round (see spec-004 PRD/实现方案) rebuilds the price foundation so
that IC / backtest conclusions can be trusted:

* :class:`SaKlineDaily`        - un-adjusted (raw) daily K, the target store.
* :class:`SaAdjustFactor`      - per-day cumulative adjust factor (hfq anchor).
* :class:`SaStockLifecycle`    - listing/delisting dates, PIT universe basis.
* :class:`SaDailyTradeStatus`  - ST/suspension/limit-up-down tradability flags.
* :class:`SaIndustryMap`       - per-stock industry mapping (multi-source).
* :class:`SaKlineSyncState`    - per-stock progress of the raw re-ingest tick.

``daily_prices`` (qfq, external legacy) stays untouched during the gray period;
reads switch via the ``settings.kline_source`` flag.
"""

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

logger = logging.getLogger(__name__)


class SaKlineDaily(Base):
    """One raw (un-adjusted) daily OHLCV bar for one stock.

    Maps the ``sa_kline_daily`` table. ``pct_change`` comes straight from the
    source and is the true post-adjustment return — the anchor the whole
    adjust-factor system is built on. ``source`` records which fetch path
    produced the row (``tencent`` / ``em``).
    """

    __tablename__ = "sa_kline_daily"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    close: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    high: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    low: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    volume: Mapped[Optional[int]] = mapped_column(BigInteger, comment="手")
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), comment="元")
    pct_change: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 4), comment="源端真实复权涨跌幅(%)，复权锚"
    )
    turnover: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4), comment="换手率(%)")
    source: Mapped[Optional[str]] = mapped_column(String(10))

    __table_args__ = (
        UniqueConstraint("stock_code", "trade_date", name="uk_code_date"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaKlineDaily(id={self.id!r}, stock_code={self.stock_code!r}, "
            f"trade_date={self.trade_date!r})"
        )


class SaAdjustFactor(Base):
    """Per-day cumulative adjust factor: ``hfq_close = raw_close * adj_factor``.

    Maps the ``sa_adjust_factor`` table. Jumps only on ex-dividend/split days.
    ``anchored=1`` rows were derived from a same-day hfq/raw fetch pair
    (exact); ``anchored=0`` rows were propagated incrementally from the
    pct_change anchor (small rounding drift, re-anchored on events).
    """

    __tablename__ = "sa_adjust_factor"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    adj_factor: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    anchored: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint("stock_code", "trade_date", name="uk_code_date"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaAdjustFactor(id={self.id!r}, stock_code={self.stock_code!r}, "
            f"trade_date={self.trade_date!r}, adj_factor={self.adj_factor!r})"
        )


class SaStockLifecycle(Base):
    """Listing/delisting window for every stock ever listed (incl. delisted).

    Maps the ``sa_stock_lifecycle`` table — the point-in-time universe basis:
    a stock is investable on date D iff ``list_date <= D < delist_date``.
    ``list_status``: ``L`` listed, ``D`` delisted, ``P`` paused.
    """

    __tablename__ = "sa_stock_lifecycle"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    stock_name: Mapped[Optional[str]] = mapped_column(String(50))
    exchange: Mapped[Optional[str]] = mapped_column(String(10))
    list_date: Mapped[Optional[date]] = mapped_column(Date)
    delist_date: Mapped[Optional[date]] = mapped_column(Date, comment="NULL=在市")
    list_status: Mapped[str] = mapped_column(String(10), nullable=False, default="L")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("stock_code", name="uk_code"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"SaStockLifecycle(id={self.id!r}, stock_code={self.stock_code!r})"


class SaDailyTradeStatus(Base):
    """Per-stock/day tradability flags (ST / suspension / limit board).

    Maps the ``sa_daily_trade_status`` table. ``limit_status`` is one of
    ``none / limit_up / limit_down / limit_up_one_word / limit_down_one_word``;
    ``buy_tradable`` is 0 on suspension or sealed one-word limit-up (cannot
    buy in), ``sell_tradable`` is 0 on suspension or sealed one-word limit-down.
    ``is_st`` is NULL where no historical name evidence exists (pre-snapshot
    years) — the filter surfaces coverage instead of guessing.
    """

    __tablename__ = "sa_daily_trade_status"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_st: Mapped[Optional[int]] = mapped_column(SmallInteger, comment="NULL=无法回溯")
    is_suspended: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    limit_status: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    buy_tradable: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    sell_tradable: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint("stock_code", "trade_date", name="uk_code_date"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaDailyTradeStatus(id={self.id!r}, stock_code={self.stock_code!r}, "
            f"trade_date={self.trade_date!r}, limit_status={self.limit_status!r})"
        )


class SaIndustryMap(Base):
    """Per-stock industry mapping with a stable code, multi-source.

    Maps the ``sa_industry_map`` table. ``industry_code`` is the stable
    identifier (eastmoney ``BKxxxx`` board code, or a SW industry code);
    ``industry_level`` distinguishes sources/granularity
    (``em`` / ``sw_l1`` / ``sw_l2``); ``effective_date`` allows the mapping to
    change over time (a row per change) so PIT grouping stays possible.
    """

    __tablename__ = "sa_industry_map"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    industry_code: Mapped[str] = mapped_column(String(20), nullable=False)
    industry_name: Mapped[str] = mapped_column(String(50), nullable=False)
    industry_level: Mapped[str] = mapped_column(String(10), nullable=False, default="em")
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("stock_code", "industry_level", "effective_date", name="uk_code_level_date"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaIndustryMap(id={self.id!r}, stock_code={self.stock_code!r}, "
            f"industry_code={self.industry_code!r})"
        )


class SaKlineSyncState(Base):
    """Per-stock progress of the ``sa_kline_daily`` full re-ingest.

    Maps the ``sa_kline_sync_state`` table — same tick/batch pattern as
    :class:`~app.models.market_data.SaHistorySyncState`. ``priority=0`` rows
    (the adjustment-break contaminated list) are drained first.
    """

    __tablename__ = "sa_kline_sync_state"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    target_start: Mapped[date] = mapped_column(Date, nullable=False)
    earliest_bar: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("stock_code", name="uk_kline_code"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaKlineSyncState(id={self.id!r}, stock_code={self.stock_code!r}, "
            f"status={self.status!r}, priority={self.priority!r})"
        )
