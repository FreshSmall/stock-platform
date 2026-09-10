"""ORM mappings for the V3a paper-trading tables.

Five tables model one simulated (paper) account end-to-end:

* :class:`SaPaperAccount`   - one paper account: strategy config + cash ledger.
* :class:`SaPaperOrder`     - daily rebalance intents (signal -> next-open exec).
* :class:`SaPaperTrade`     - filled executions with the per-item fee breakdown.
* :class:`SaPaperPosition`  - end-of-day per-stock holding snapshot.
* :class:`SaPaperNav`       - end-of-day account NAV / benchmark series.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SaPaperAccount(Base):
    """A paper-trading account: strategy config plus the cash ledger."""

    __tablename__ = "sa_paper_account"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False)
    initial_cash: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=func.text("'active'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (UniqueConstraint("name", name="uk_name"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaPaperAccount(id={self.id!r}, name={self.name!r}, "
            f"status={self.status!r})"
        )


class SaPaperOrder(Base):
    """One rebalance intent, emitted on ``signal_date`` for next-open exec."""

    __tablename__ = "sa_paper_order"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sa_paper_account.id"), nullable=False
    )
    signal_date: Mapped[date] = mapped_column(Date, nullable=False)
    exec_date: Mapped[date] = mapped_column(Date, nullable=False)
    stock_code: Mapped[str] = mapped_column(String(12), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    target_weight: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    shares: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default=func.text("'pending'")
    )
    fail_reason: Mapped[Optional[str]] = mapped_column(String(64))
    filled_trade_id: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "signal_date",
            "stock_code",
            "side",
            name="uk_account_signal_code_side",
        ),
        Index("ix_account_exec", "account_id", "exec_date"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaPaperOrder(id={self.id!r}, account_id={self.account_id!r}, "
            f"stock_code={self.stock_code!r}, side={self.side!r}, "
            f"status={self.status!r})"
        )


class SaPaperTrade(Base):
    """A filled paper execution with its per-item fee breakdown."""

    __tablename__ = "sa_paper_trade"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sa_paper_account.id"), nullable=False
    )
    order_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sa_paper_order.id"), nullable=False
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    stock_code: Mapped[str] = mapped_column(String(12), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    shares: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    commission: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    stamp_duty: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    transfer_fee: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_account_trade_date", "account_id", "trade_date"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaPaperTrade(id={self.id!r}, account_id={self.account_id!r}, "
            f"stock_code={self.stock_code!r}, side={self.side!r}, "
            f"amount={self.amount!r})"
        )


class SaPaperPosition(Base):
    """End-of-day per-stock holding snapshot for one account."""

    __tablename__ = "sa_paper_position"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sa_paper_account.id"), nullable=False
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    stock_code: Mapped[str] = mapped_column(String(12), nullable=False)
    shares: Mapped[int] = mapped_column(Integer, nullable=False)
    close_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    market_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "account_id", "trade_date", "stock_code", name="uk_account_date_code"
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaPaperPosition(id={self.id!r}, account_id={self.account_id!r}, "
            f"trade_date={self.trade_date!r}, stock_code={self.stock_code!r})"
        )


class SaPaperNav(Base):
    """End-of-day account NAV series with benchmark and drawdown."""

    __tablename__ = "sa_paper_nav"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sa_paper_account.id"), nullable=False
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    market_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    total_asset: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    nav: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)
    daily_ret: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 8))
    benchmark_close: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))
    benchmark_nav: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 6))
    drawdown: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))

    __table_args__ = (
        UniqueConstraint("account_id", "trade_date", name="uk_account_date"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaPaperNav(id={self.id!r}, account_id={self.account_id!r}, "
            f"trade_date={self.trade_date!r}, nav={self.nav!r})"
        )
