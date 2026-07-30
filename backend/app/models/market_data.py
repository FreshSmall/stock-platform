"""ORM mappings for V1.5 market-data ``sa_`` tables.

Contains the application-managed tables introduced in V1.5 to widen the data
surface beyond the V1 daily-K + financial baseline:

* :class:`SaMinutePrice`       - intraday minute K-line bars.
* :class:`SaDragonTiger`       - dragon-tiger (龙虎榜) listed stocks per day.
* :class:`SaDragonTigerSeat`   - top-5 buy/sell seats for a dragon-tiger stock.
* :class:`SaNorthFlow`         - daily northbound (沪深股通) net inflow.
* :class:`SaMoneyFlowDetail`   - super/big/medium/small order net inflow detail.
* :class:`SaAdminTaskLog`      - acquisition task execution log (for admin).
* :class:`SaStockIndustry`     - industry supplement (stock_pool is read-only).

All are owned and written by this service; migrated by Alembic.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SaMinutePrice(Base):
    """One OHLCV bar at a given minute period for one stock.

    Maps the ``sa_minute_price`` table. ``period`` is the bar size in minutes
    (1/5/15/30/60/120). ``trade_time`` is the exact minute timestamp.
    """

    __tablename__ = "sa_minute_price"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    trade_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    period: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    open: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    close: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    high: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    low: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    volume: Mapped[Optional[int]] = mapped_column(BigInteger)
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))

    __table_args__ = (
        UniqueConstraint("stock_code", "period", "trade_time", name="uk_code_period_time"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaMinutePrice(id={self.id!r}, stock_code={self.stock_code!r}, "
            f"period={self.period!r}, trade_time={self.trade_time!r})"
        )


class SaDragonTiger(Base):
    """A stock listed on the dragon-tiger board (龙虎榜) for one trade day.

    Maps the ``sa_dragon_tiger`` table.
    """

    __tablename__ = "sa_dragon_tiger"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    stock_name: Mapped[Optional[str]] = mapped_column(String(50))
    reason: Mapped[Optional[str]] = mapped_column(String(100))
    net_buy: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    buy_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    sell_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))

    __table_args__ = (
        UniqueConstraint("trade_date", "stock_code", name="uk_date_code"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaDragonTiger(id={self.id!r}, trade_date={self.trade_date!r}, "
            f"stock_code={self.stock_code!r})"
        )


class SaDragonTigerSeat(Base):
    """One of the top-5 buy/sell seats for a dragon-tiger stock.

    Maps the ``sa_dragon_tiger_seat`` table. ``side`` is 1=buy, 2=sell;
    ``rank`` is the seat ordinal (1-5); ``is_institution`` flags 机构席位.
    """

    __tablename__ = "sa_dragon_tiger_seat"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    side: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    rank: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    seat_name: Mapped[str] = mapped_column(String(100), nullable=False)
    buy_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    sell_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    net_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    is_institution: Mapped[Optional[int]] = mapped_column(SmallInteger, default=0)

    __table_args__ = (
        UniqueConstraint(
            "trade_date", "stock_code", "side", "rank", name="uk_date_code_side_rank"
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaDragonTigerSeat(id={self.id!r}, stock_code={self.stock_code!r}, "
            f"side={self.side!r}, rank={self.rank!r})"
        )


class SaNorthFlow(Base):
    """Daily northbound net inflow for one channel (沪股通/深股通).

    Maps the ``sa_north_flow`` table. ``channel`` is ``sh`` or ``sz``.
    """

    __tablename__ = "sa_north_flow"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    net_buy: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    buy_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    sell_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))

    __table_args__ = (
        UniqueConstraint("trade_date", "channel", name="uk_date_channel"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaNorthFlow(id={self.id!r}, trade_date={self.trade_date!r}, "
            f"channel={self.channel!r})"
        )


class SaMoneyFlowDetail(Base):
    """Four-tier (super/big/medium/small order) net inflow for one stock/day.

    Maps the ``sa_money_flow_detail`` table. Super + big together approximate
    the main-force net inflow stored in :class:`~app.models.finance.SaMoneyFlow`.
    """

    __tablename__ = "sa_money_flow_detail"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    super_net: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    big_net: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    medium_net: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    small_net: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))

    __table_args__ = (
        UniqueConstraint("stock_code", "trade_date", name="uk_code_date"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaMoneyFlowDetail(id={self.id!r}, stock_code={self.stock_code!r}, "
            f"trade_date={self.trade_date!r})"
        )


class SaAdminTaskLog(Base):
    """Execution log for an acquisition task, surfaced in the admin console.

    Maps the ``sa_admin_task_log`` table. ``triggered_by`` is either ``scheduler``
    or ``manual:<username>``. ``status`` is running/success/failed.
    """

    __tablename__ = "sa_admin_task_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_name: Mapped[str] = mapped_column(String(50), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    rows_affected: Mapped[Optional[int]] = mapped_column(Integer)
    error: Mapped[Optional[str]] = mapped_column(Text)
    triggered_by: Mapped[Optional[str]] = mapped_column(String(50))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaAdminTaskLog(id={self.id!r}, task_name={self.task_name!r}, "
            f"status={self.status!r})"
        )


class SaStockIndustry(Base):
    """Industry supplement for a stock.

    Maps the ``sa_stock_industry`` table. ``stock_pool`` is read-only by
    convention, so missing ``industry`` values are stored here and joined in at
    query time rather than written back to ``stock_pool``.
    """

    __tablename__ = "sa_stock_industry"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    industry: Mapped[Optional[str]] = mapped_column(String(50))
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
        return f"SaStockIndustry(id={self.id!r}, stock_code={self.stock_code!r})"


class SaIndexQuote(Base):
    """Daily quote for a market index (上证/深证/创业板指).

    Maps the ``sa_index_quote`` table. ``index_code`` carries the exchange
    prefix (e.g. ``sh000001``) so it never collides with a stock code in
    ``daily_prices`` (where ``000001`` is 平安银行, not 上证指数). ``pct_change``
    is computed at ingest time from consecutive closes.
    """

    __tablename__ = "sa_index_quote"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    index_code: Mapped[str] = mapped_column(String(20), nullable=False)
    index_name: Mapped[Optional[str]] = mapped_column(String(50))
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    close: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    high: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    low: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    pct_change: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))

    __table_args__ = (
        UniqueConstraint("index_code", "trade_date", name="uk_index_date"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaIndexQuote(id={self.id!r}, index_code={self.index_code!r}, "
            f"trade_date={self.trade_date!r}, close={self.close!r})"
        )
