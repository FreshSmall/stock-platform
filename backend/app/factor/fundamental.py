"""Fundamental factors (BP-V2-001).

PE/PB/market-cap from stock_pool; ROE/EPS/growth from sa_financial_extra (V1).
All read pre-computed values — no on-the-fly calculation. Lookups go through
the session-scoped caches in :mod:`app.factor.cache` (N+1 fix).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.factor import cache as fcache
from app.factor.base import Factor, registry
from app.models.finance import SaFinancialExtra
from app.models.stock import StockPool


def _pool_field(db: Session, stock: str, trade_date: date, field) -> float | None:
    """Latest stock_pool value for ``field`` on or before ``trade_date``."""
    row = fcache.latest_le(fcache.pool_rows_for(db, stock), trade_date)
    if row is None:
        return None
    v = getattr(row, field.key)
    return float(v) if v is not None else None


def _fin_field(db: Session, stock: str, field) -> float | None:
    """Latest sa_financial_extra value for ``field`` (most recent report)."""
    rows = fcache.fin_rows_for(db, stock)
    if not rows:
        return None
    v = getattr(rows[-1], field.key)
    return float(v) if v is not None else None


class PeFactor(Factor):
    code = "pe"
    name = "市盈率PE"
    category = "fundamental"

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        return _pool_field(db, stock, trade_date, StockPool.pe)


class PbFactor(Factor):
    code = "pb"
    name = "市净率PB"
    category = "fundamental"

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        return _pool_field(db, stock, trade_date, StockPool.pb)


class MarketCapFactor(Factor):
    code = "total_mv"
    name = "总市值"
    category = "fundamental"

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        return _pool_field(db, stock, trade_date, StockPool.total_mv)


class RoeFactor(Factor):
    code = "roe"
    name = "净资产收益率ROE"
    category = "fundamental"

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        return _fin_field(db, stock, SaFinancialExtra.roe)


class EpsFactor(Factor):
    code = "eps"
    name = "每股收益EPS"
    category = "fundamental"

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        return _fin_field(db, stock, SaFinancialExtra.eps)


class RevenueGrowthFactor(Factor):
    code = "revenue_growth"
    name = "营收增长率"
    category = "fundamental"

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        return _fin_field(db, stock, SaFinancialExtra.revenue_growth)


class ProfitGrowthFactor(Factor):
    code = "profit_growth"
    name = "净利润增长率"
    category = "fundamental"

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        return _fin_field(db, stock, SaFinancialExtra.profit_growth)


for _cls in (
    PeFactor, PbFactor, MarketCapFactor,
    RoeFactor, EpsFactor, RevenueGrowthFactor, ProfitGrowthFactor,
):
    registry.register(_cls())
