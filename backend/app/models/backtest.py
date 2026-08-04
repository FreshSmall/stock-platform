"""ORM mappings for backtest tables.

Contains:

* :class:`SaBacktestRun`   - one backtest execution (params + status).
* :class:`SaBacktestResult` - the aggregated metrics/trades for a run.

Both share the ``run_id`` business key (1:1 relationship). The metrics live
in a separate table so the heavy ``equity_curve``/``trades`` JSON columns are
only loaded when needed.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SaBacktestRun(Base):
    """One backtest execution: input parameters plus lifecycle status."""

    __tablename__ = "sa_backtest_run"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    strategy: Mapped[str] = mapped_column(String(20), nullable=False)
    params: Mapped[dict] = mapped_column(JSON, nullable=False)
    stock_pool: Mapped[dict] = mapped_column(JSON, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    initial_cash: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    commission: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), nullable=False, server_default=func.text("0.0003")
    )
    slippage: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), nullable=False, server_default=func.text("0.0001")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=func.text("'pending'")
    )
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    __table_args__ = (Index("idx_user", "user_id"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaBacktestRun(id={self.id!r}, run_id={self.run_id!r}, "
            f"strategy={self.strategy!r}, status={self.status!r})"
        )


class SaBacktestResult(Base):
    """Aggregated metrics + trades for one :class:`SaBacktestRun`."""

    __tablename__ = "sa_backtest_result"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    return_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    max_drawdown: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    sharpe: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    win_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    # V2 advanced metrics
    calmar: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    information_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    profit_loss_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    equity_curve: Mapped[Optional[dict]] = mapped_column(JSON)
    drawdown_curve: Mapped[Optional[dict]] = mapped_column(JSON)
    position_curve: Mapped[Optional[dict]] = mapped_column(JSON)
    benchmark_curve: Mapped[Optional[dict]] = mapped_column(JSON)
    benchmark_return: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    trades: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaBacktestResult(id={self.id!r}, run_id={self.run_id!r}, "
            f"return_rate={self.return_rate!r})"
        )
