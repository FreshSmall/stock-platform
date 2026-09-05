"""Volatility factors (BP-V2-001).

BOLL width reuses V1.5 indicator_service; ATR and HV (historical volatility)
are implemented here.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.factor.base import Factor, FactorParam, registry
from app.factor.trend import _closes_up_to, _WINDOW
from app.services import indicator_service, market_service


class BollWidthFactor(Factor):
    """Bollinger band width = (upper - lower) / mid — a volatility proxy."""

    code = "boll_width"
    name = "布林带宽度"
    category = "volatility"
    params = [FactorParam("n", 20, min=5, max=50, description="布林周期")]

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        n = self.default_kwargs()["n"]
        closes = _closes_up_to(db, stock, trade_date)
        if len(closes) < n:
            return None
        df = indicator_service.calc_boll(closes, n=n)
        up, mid, low = df["boll_up"].iloc[-1], df["boll_mid"].iloc[-1], df["boll_down"].iloc[-1]
        if pd.isna(up) or pd.isna(mid) or mid == 0:
            return None
        return float((up - low) / mid)


class AtrFactor(Factor):
    """Average True Range (Wilder), normalised by close for cross-stock scale."""

    code = "atr14"
    name = "ATR平均真实波幅"
    category = "volatility"
    params = [FactorParam("period", 14, min=5, max=50, description="ATR周期")]

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        period = self.default_kwargs()["period"]
        rows = market_service.get_kline(db, stock, end=trade_date)
        if not rows:
            return None
        highs = pd.Series([float(r.high) for r in rows if r.high is not None], dtype=float).tail(_WINDOW)
        lows = pd.Series([float(r.low) for r in rows if r.low is not None], dtype=float).tail(_WINDOW)
        closes = pd.Series([float(r.close) for r in rows if r.close is not None], dtype=float).tail(_WINDOW)
        if len(closes) < period:
            return None
        tr = pd.concat(
            [highs - lows, (highs - closes.shift()).abs(), (lows - closes.shift()).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        last_atr = atr.iloc[-1]
        last_close = closes.iloc[-1]
        if pd.isna(last_atr) or last_close == 0:
            return None
        return float(last_atr / last_close)  # normalised


class HvFactor(Factor):
    """Historical volatility: std of log returns over a window, annualised."""

    code = "hv20"
    name = "历史波动率"
    category = "volatility"
    params = [FactorParam("period", 20, min=5, max=60, description="波动率窗口")]

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        period = self.default_kwargs()["period"]
        closes = _closes_up_to(db, stock, trade_date, n=period + 5)
        if len(closes) < period + 1:
            return None
        logret = np.log(closes / closes.shift(1))
        hv = logret.rolling(window=period, min_periods=period).std()
        v = hv.iloc[-1]
        if pd.isna(v):
            return None
        return float(v * np.sqrt(250))  # annualised


registry.register(BollWidthFactor())
registry.register(AtrFactor())
registry.register(HvFactor())


class SkewFactor(Factor):
    """Rolling skewness of daily log returns.

    Negative-IC name in the 2026-08 survey (A-share reversal regime): stocks
    with a right-skewed recent return distribution tend to underperform —
    a few big up-days propping up an otherwise flat series.
    """

    code = "skew20"
    name = "20日收益偏度"
    category = "volatility"
    params = [FactorParam("period", 20, min=5, max=60, description="偏度窗口")]

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        period = self.default_kwargs()["period"]
        closes = _closes_up_to(db, stock, trade_date, n=period + 5)
        if len(closes) < period + 1:
            return None
        logret = np.log(closes / closes.shift(1))
        v = logret.rolling(window=period, min_periods=period).skew().iloc[-1]
        return None if pd.isna(v) else float(v)


registry.register(SkewFactor())
