"""ORM mappings for the EXISTING read-only tables in ``stock_analysis``.

These tables are populated by external data pipelines. We map them so the
service can read them through SQLAlchemy 2.0 typed constructs, but we never
write to them. Each mapping carries ``__table_args__ = {"info": {"readonly":
True}}`` to document that intent (this is a project convention; SQLAlchemy
itself does not enforce read-only-ness).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import BigInteger, Date, DateTime, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DailyPrice(Base):
    """A single OHLCV bar for one stock on one trading day.

    Maps the ``daily_prices`` table (≈1.1M rows including stock 600519 贵州茅台).
    """

    __tablename__ = "daily_prices"
    __table_args__ = ({"info": {"readonly": True}},)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    close: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    high: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    low: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    volume: Mapped[Optional[int]] = mapped_column(BigInteger)
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    pct_change: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    turnover: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"DailyPrice(id={self.id!r}, stock_code={self.stock_code!r}, "
            f"trade_date={self.trade_date!r}, close={self.close!r})"
        )


class StockPool(Base):
    """A snapshot row in a named stock pool (筛选结果快照).

    Maps the ``stock_pool`` table (≈8456 rows).
    """

    __tablename__ = "stock_pool"
    __table_args__ = ({"info": {"readonly": True}},)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pool_name: Mapped[str] = mapped_column(String(50), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    stock_name: Mapped[Optional[str]] = mapped_column(String(50))
    exchange: Mapped[Optional[str]] = mapped_column(String(10))
    industry: Mapped[Optional[str]] = mapped_column(String(50))
    close: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 3))
    pct_change: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    total_mv: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    circ_mv: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    turnover: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    pe: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 3))
    pb: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 3))
    list_date: Mapped[Optional[date]] = mapped_column(Date)
    audit_opinion: Mapped[Optional[str]] = mapped_column(String(50))
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"StockPool(id={self.id!r}, pool_name={self.pool_name!r}, "
            f"trade_date={self.trade_date!r}, stock_code={self.stock_code!r})"
        )


class ChipDistribution(Base):
    """Chip-distribution (CYQ) snapshot for one stock on one trade day.

    Maps the read-only ``chip_distribution`` table (populated by an external
    CYQ pipeline, ≈390k rows). ``distribution`` is a JSON/text histogram of
    share count by price; the scalar fields (``profit_ratio``, ``avg_cost``,
    ``concentration_90``) are pre-computed rollups consumed directly by the
    chip-peak display (BP-V1.5-007). Read-only: never written by this service.
    """

    __tablename__ = "chip_distribution"
    __table_args__ = ({"info": {"readonly": True}},)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    trade_date: Mapped[Optional[date]] = mapped_column(Date)
    profit_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 6))
    avg_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 3))
    cost_90_low: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 3))
    cost_90_high: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 3))
    concentration_90: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    cost_70_low: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 3))
    cost_70_high: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 3))
    concentration_70: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    distribution: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"ChipDistribution(id={self.id!r}, stock_code={self.stock_code!r}, "
            f"trade_date={self.trade_date!r})"
        )
