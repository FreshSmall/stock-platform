"""ORM for V2 factor tables.

* :class:`SaFactorValue` — materialised factor value per (factor, stock, date).
* :class:`SaFactorIc`    — factor effectiveness test (IC/IR/win-rate/layered).
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
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.mysql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SaFactorValue(Base):
    """One materialised factor value for one stock on one trade day.

    Populated lazily — only when IC testing or multi-factor scoring needs it
    persisted. Technical factors are usually computed on the fly.
    """

    __tablename__ = "sa_factor_value"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    factor_code: Mapped[str] = mapped_column(String(30), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("factor_code", "stock_code", "trade_date", name="uk_factor_code_date"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"SaFactorValue(id={self.id!r}, factor_code={self.factor_code!r}, "
            f"stock_code={self.stock_code!r}, trade_date={self.trade_date!r})"
        )


class SaFactorIc(Base):
    """Factor effectiveness test result for one rebalance date + horizon.

    IC = Spearman rank correlation between the factor value and the forward
    N-day return across the stock universe. IR = mean(IC) / std(IC).
    """

    __tablename__ = "sa_factor_ic"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    factor_code: Mapped[str] = mapped_column(String(30), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizon: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    ic: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    ir: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    win_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    layered_returns: Mapped[Optional[dict]] = mapped_column(JSON)

    __table_args__ = (
        UniqueConstraint(
            "factor_code", "trade_date", "horizon", name="uk_factor_date_horizon"
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover:
        return (
            f"SaFactorIc(id={self.id!r}, factor_code={self.factor_code!r}, "
            f"trade_date={self.trade_date!r}, horizon={self.horizon!r})"
        )
