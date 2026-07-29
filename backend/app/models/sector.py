"""ORM mappings for the V1.5 sector (板块) ``sa_`` tables.

Contains:

* :class:`SaSector`       - sector definition (industry or concept).
* :class:`SaSectorStock`  - sector membership (which stocks belong to a sector).
* :class:`SaSectorDaily`  - per-sector daily aggregate stats (change/amount/...).

All are owned and written by this service; migrated by Alembic.
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
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SaSector(Base):
    """A sector (industry or concept) definition.

    Maps the ``sa_sector`` table. ``sector_type`` is ``industry`` or ``concept``.
    """

    __tablename__ = "sa_sector"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sector_code: Mapped[str] = mapped_column(String(20), nullable=False)
    sector_name: Mapped[str] = mapped_column(String(50), nullable=False)
    sector_type: Mapped[str] = mapped_column(String(10), nullable=False)

    __table_args__ = (
        UniqueConstraint("sector_code", "sector_type", name="uk_code_type"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaSector(id={self.id!r}, sector_code={self.sector_code!r}, "
            f"sector_type={self.sector_type!r})"
        )


class SaSectorStock(Base):
    """Membership: a stock belongs to a sector.

    Maps the ``sa_sector_stock`` table.
    """

    __tablename__ = "sa_sector_stock"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sector_code: Mapped[str] = mapped_column(String(20), nullable=False)
    sector_type: Mapped[str] = mapped_column(String(10), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(10), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "sector_code", "sector_type", "stock_code", name="uk_sector_stock"
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaSectorStock(id={self.id!r}, sector_code={self.sector_code!r}, "
            f"stock_code={self.stock_code!r})"
        )


class SaSectorDaily(Base):
    """Per-sector daily aggregate statistics.

    Maps the ``sa_sector_daily`` table. ``leader_code`` is the day's top gainer
    in the sector; ``main_net_inflow`` is the sector's main-force net inflow.
    """

    __tablename__ = "sa_sector_daily"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sector_code: Mapped[str] = mapped_column(String(20), nullable=False)
    sector_type: Mapped[str] = mapped_column(String(10), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    pct_change: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    limit_up_count: Mapped[Optional[int]] = mapped_column(Integer)
    main_net_inflow: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2))
    leader_code: Mapped[Optional[str]] = mapped_column(String(10))

    __table_args__ = (
        UniqueConstraint(
            "sector_code", "sector_type", "trade_date", name="uk_sector_date"
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaSectorDaily(id={self.id!r}, sector_code={self.sector_code!r}, "
            f"trade_date={self.trade_date!r})"
        )
