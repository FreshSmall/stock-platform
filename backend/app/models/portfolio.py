"""ORM for V2 portfolio tables (BP-V2-005).

* :class:`SaPortfolio`        — a named user portfolio (benchmark + metadata).
* :class:`SaPortfolioHolding` — one stock+weight in a portfolio.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SaPortfolio(Base):
    """A user-defined stock portfolio."""

    __tablename__ = "sa_portfolio"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(200))
    benchmark: Mapped[str] = mapped_column(String(20), nullable=False, default="sh000001")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"SaPortfolio(id={self.id!r}, name={self.name!r})"


class SaPortfolioHolding(Base):
    """One stock + weight in a portfolio."""

    __tablename__ = "sa_portfolio_holding"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)
    weight: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False, default=Decimal("0.5"))
    added_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("portfolio_id", "stock_code", name="uk_pf_stock"),
    )

    def __repr__(self) -> str:  # pragma: no cover:
        return f"SaPortfolioHolding(id={self.id!r}, portfolio_id={self.portfolio_id!r}, stock_code={self.stock_code!r})"
