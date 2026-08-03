"""Trend factors (BP-V2-001).

Each factor computes a single value for (stock, trade_date) by pulling the
daily-K window up to that date and reducing the indicator to its latest bar.
Technical factors are computed on the fly — no persistence unless IC testing
asks for it.

Implemented:
- MA (5/10/20 close), EMA (12/26), MACD (dif/dea/hist), ADX, SuperTrend.
"""

from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.factor.base import Factor, FactorParam, registry
from app.models.stock import DailyPrice
from app.services import indicator_service, market_service

logger = logging.getLogger(__name__)

# How many trailing daily bars to load for a single factor compute. Enough for
# the slowest default window (26-period EMA / 20-period BOLL) plus headroom.
_WINDOW = 120


def _closes_up_to(db: Session, stock: str, trade_date: date, n: int = _WINDOW) -> pd.Series:
    """Close-price series ending at ``trade_date`` (inclusive), ascending."""
    rows = market_service.get_kline(db, stock, end=trade_date)
    if not rows:
        return pd.Series(dtype=float)
    closes = pd.Series(
        [float(r.close) for r in rows if r.close is not None],
        dtype=float,
    )
    return closes.tail(n).reset_index(drop=True)


def _last_or_none(series: pd.Series) -> float | None:
    """Return the last non-NaN value, or None."""
    if series is None or series.empty:
        return None
    v = series.iloc[-1]
    return None if pd.isna(v) else float(v)


class MaFactor(Factor):
    code = "ma5"
    name = "MA5均线"
    category = "trend"
    params = [FactorParam("period", 5, min=2, max=250, description="均线周期")]

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        period = self.default_kwargs()["period"]
        closes = _closes_up_to(db, stock, trade_date)
        if len(closes) < period:
            return None
        df = indicator_service.calc_ma(closes, periods=[period])
        return _last_or_none(df[f"ma{period}"])


class EmaFactor(Factor):
    code = "ema12"
    name = "EMA12指数均线"
    category = "trend"
    params = [FactorParam("period", 12, min=2, max=120, description="EMA周期")]

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        period = self.default_kwargs()["period"]
        closes = _closes_up_to(db, stock, trade_date)
        if closes.empty:
            return None
        s = indicator_service.calc_ema(closes, period)
        self.code = f"ema{period}"  # noqa - keep code in sync with param
        return _last_or_none(s)


class MacdDifFactor(Factor):
    code = "macd_dif"
    name = "MACD-DIF"
    category = "trend"

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        closes = _closes_up_to(db, stock, trade_date)
        if closes.empty:
            return None
        df = indicator_service.calc_macd(closes)
        return _last_or_none(df["dif"])


class AdxFactor(Factor):
    """ADX (Average Directional Index) — trend strength, 0-100.

    Uses the classic 14-period Wilder smoothing. ADX > 25 indicates a strong
    trend. Implemented here because V1.5 has no ADX indicator.
    """

    code = "adx14"
    name = "ADX趋势强度"
    category = "trend"
    params = [FactorParam("period", 14, min=5, max=50, description="ADX周期")]

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        period = self.default_kwargs()["period"]
        rows = market_service.get_kline(db, stock, end=trade_date)
        if not rows:
            return None
        highs = pd.Series([float(r.high) for r in rows if r.high is not None], dtype=float).tail(_WINDOW)
        lows = pd.Series([float(r.low) for r in rows if r.low is not None], dtype=float).tail(_WINDOW)
        closes = pd.Series([float(r.close) for r in rows if r.close is not None], dtype=float).tail(_WINDOW)
        if len(closes) < period * 2:
            return None
        adx = _calc_adx(highs, lows, closes, period)
        return _last_or_none(adx)


def _calc_adx(highs: pd.Series, lows: pd.Series, closes: pd.Series, period: int = 14) -> pd.Series:
    """ADX via Wilder smoothing (no TA-Lib)."""
    up = highs.diff()
    down = -lows.diff()
    plus_dm = (up > down) & (up > 0)
    plus_dm = up.where(plus_dm, 0.0)
    minus_dm = (down > up) & (down > 0)
    minus_dm = down.where(minus_dm, 0.0)
    tr = pd.concat(
        [highs - lows, (highs - closes.shift()).abs(), (lows - closes.shift()).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


class SuperTrendFactor(Factor):
    """SuperTrend direction: +1 = uptrend (green), -1 = downtrend (red), 0 = N/A.

    Built on ATR(10) and a 3x multiplier. Returns the sign so it can be used as
    a categorical trend factor; the price level itself is not comparable across
    stocks.
    """

    code = "supertrend"
    name = "SuperTrend趋势方向"
    category = "trend"
    params = [
        FactorParam("period", 10, min=5, max=50, description="ATR周期"),
        FactorParam("multiplier", 3.0, min=1.0, max=6.0, description="ATR倍数"),
    ]

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        period = self.default_kwargs()["period"]
        mult = self.default_kwargs()["multiplier"]
        rows = market_service.get_kline(db, stock, end=trade_date)
        if not rows:
            return None
        highs = pd.Series([float(r.high) for r in rows if r.high is not None], dtype=float).tail(_WINDOW)
        lows = pd.Series([float(r.low) for r in rows if r.low is not None], dtype=float).tail(_WINDOW)
        closes = pd.Series([float(r.close) for r in rows if r.close is not None], dtype=float).tail(_WINDOW)
        if len(closes) < period * 2:
            return None
        sign = _calc_supertrend(highs, lows, closes, period, mult)
        v = sign.iloc[-1]
        return None if pd.isna(v) else float(v)


def _calc_supertrend(
    highs: pd.Series, lows: pd.Series, closes: pd.Series, period: int = 10, mult: float = 3.0
) -> pd.Series:
    """SuperTrend sign series (+1 up / -1 down). Simplified single-pass."""
    tr = pd.concat(
        [highs - lows, (highs - closes.shift()).abs(), (lows - closes.shift()).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    hl2 = (highs + lows) / 2.0
    upper = hl2 + mult * atr
    lower = hl2 - mult * atr
    sign = pd.Series(np.nan, index=closes.index)
    in_up = True
    for i in range(1, len(closes)):
        if closes.iloc[i] > upper.iloc[i - 1]:
            in_up = True
        elif closes.iloc[i] < lower.iloc[i - 1]:
            in_up = False
        sign.iloc[i] = 1.0 if in_up else -1.0
    return sign


# --- registration ---------------------------------------------------------
# Multiple MA/EMA periods registered as distinct factors.
for _p in (5, 10, 20):
    _f = MaFactor()
    _f.code = f"ma{_p}"
    _f.name = f"MA{_p}均线"
    _f.params = [FactorParam("period", _p, min=2, max=250, description="均线周期")]
    registry.register(_f)

for _p in (12, 26):
    _f = EmaFactor()
    _f.code = f"ema{_p}"
    _f.name = f"EMA{_p}指数均线"
    _f.params = [FactorParam("period", _p, min=2, max=120, description="EMA周期")]
    registry.register(_f)

registry.register(MacdDifFactor())
registry.register(AdxFactor())
registry.register(SuperTrendFactor())
