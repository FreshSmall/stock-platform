"""Volume factors (BP-V2-001).

OBV, volume-ratio (量比), turnover (换手率), volume-price ratio.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy.orm import Session

from app.factor import cache as fcache
from app.factor.base import Factor, FactorParam, registry
from app.services import market_service


class ObvFactor(Factor):
    """On-Balance Volume trend sign (1 if rising, -1 if falling)."""

    code = "obv_trend"
    name = "OBV趋势方向"
    category = "volume"

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        rows = market_service.get_kline(db, stock, end=trade_date)
        r = [x for x in rows if x.close is not None and x.volume is not None]
        if len(r) < 20:
            return None
        closes = pd.Series([float(x.close) for x in r], dtype=float).tail(60)
        vols = pd.Series([float(x.volume) for x in r], dtype=float).tail(60)
        direction = closes.diff().fillna(0).apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        obv = (direction * vols).cumsum()
        # sign of short-term slope (last vs 5 bars ago)
        if len(obv) < 6:
            return None
        diff = obv.iloc[-1] - obv.iloc[-6]
        return 1.0 if diff > 0 else (-1.0 if diff < 0 else 0.0)


class VolRatioFactor(Factor):
    """量比 = today's volume / avg volume of last N days."""

    code = "vol_ratio5"
    name = "量比(5日)"
    category = "volume"
    params = [FactorParam("period", 5, min=2, max=20, description="均量周期")]

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        period = self.default_kwargs()["period"]
        rows = market_service.get_kline(db, stock, end=trade_date)
        vols = [float(x.volume) for x in rows if x.volume is not None]
        if len(vols) < period + 1:
            return None
        today = vols[-1]
        avg = sum(vols[-period - 1 : -1]) / period
        if avg == 0:
            return None
        return float(today / avg)


class TurnoverFactor(Factor):
    """换手率 from the latest stock_pool snapshot."""

    code = "turnover"
    name = "换手率"
    category = "volume"

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        row = fcache.latest_le(fcache.pool_rows_for(db, stock), trade_date)
        if row is None or row.turnover is None:
            return None
        return float(row.turnover)


class VolPriceFactor(Factor):
    """量价同向度: +1 量价齐升, -1 量价齐跌, 0 背离/中性."""

    code = "vol_price_trend"
    name = "量价同向度"
    category = "volume"

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        rows = market_service.get_kline(db, stock, end=trade_date)
        r = [x for x in rows if x.close is not None and x.volume is not None]
        if len(r) < 6:
            return None
        closes = pd.Series([float(x.close) for x in r], dtype=float).tail(6)
        vols = pd.Series([float(x.volume) for x in r], dtype=float).tail(6)
        price_up = closes.iloc[-1] > closes.iloc[0]
        vol_up = vols.iloc[-1] > vols.iloc[0]
        if price_up and vol_up:
            return 1.0
        if (not price_up) and (not vol_up):
            return -1.0
        return 0.0


registry.register(ObvFactor())
for _p in (5, 10):
    _f = VolRatioFactor()
    _f.code = f"vol_ratio{_p}"
    _f.name = f"量比({_p}日)"
    _f.params = [FactorParam("period", _p, min=2, max=20, description="均量周期")]
    registry.register(_f)
registry.register(TurnoverFactor())
registry.register(VolPriceFactor())
