"""ORM mappings for finance-related tables.

Contains:

* :class:`SaMoneyFlow`      - daily main-force net inflow per stock.
* :class:`SaFinancialExtra` - supplementary per-report financial indicators.

Both are keyed by (``stock_code``, date) with a unique constraint, and provide
the data the AI-analysis pipeline joins with fundamentals.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SaMoneyFlow(Base):
    """Daily main-force net inflow for one stock.

    Maps the ``sa_money_flow`` table; populated by the data-acquisition
    pipeline and consumed by the AI-analysis capital-flow scoring.
    """

    __tablename__ = "sa_money_flow"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    main_net_inflow: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))

    __table_args__ = (
        UniqueConstraint("stock_code", "trade_date", name="uk_code_date"),
        Index("idx_date", "trade_date"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaMoneyFlow(id={self.id!r}, stock_code={self.stock_code!r}, "
            f"trade_date={self.trade_date!r}, main_net_inflow={self.main_net_inflow!r})"
        )


class SaFinancialExtra(Base):
    """Supplementary per-report financial indicators for one stock.

    Maps the ``sa_financial_extra`` table. ``updated_at`` is bumped
    automatically on every row update (``ON UPDATE CURRENT_TIMESTAMP``).
    """

    __tablename__ = "sa_financial_extra"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    roe: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    eps: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    revenue_growth: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    profit_growth: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("stock_code", "report_date", name="uk_code_report"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaFinancialExtra(id={self.id!r}, stock_code={self.stock_code!r}, "
            f"report_date={self.report_date!r})"
        )
