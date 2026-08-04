"""Momentum factors (BP-V2-001).

RSI/KDJ reuse V1.5 indicator_service; ROC/CCI are implemented here.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy.orm import Session

from app.factor.base import Factor, FactorParam, registry
from app.factor.trend import _closes_up_to, _last_or_none, _WINDOW
from app.services import indicator_service, market_service


class RsiFactor(Factor):
    code = "rsi14"
    name = "RSI14相对强弱"
    category = "momentum"
    params = [FactorParam("period", 14, min=2, max=50, description="RSI周期")]

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        period = self.default_kwargs()["period"]
        closes = _closes_up_to(db, stock, trade_date)
        if closes.empty:
            return None
        df = indicator_service.calc_rsi(closes, periods=[period])
        return _last_or_none(df[f"rsi{period}"])


class KdjKFactor(Factor):
    code = "kdj_k"
    name = "KDJ-K值"
    category = "momentum"

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        rows = market_service.get_kline(db, stock, end=trade_date)
        if not rows:
            return None
        r = [x for x in rows if x.close is not None]
        highs = pd.Series([float(x.high) for x in r], dtype=float).tail(_WINDOW)
        lows = pd.Series([float(x.low) for x in r], dtype=float).tail(_WINDOW)
        closes = pd.Series([float(x.close) for x in r], dtype=float).tail(_WINDOW)
        if closes.empty:
            return None
        df = indicator_service.calc_kdj(highs, lows, closes)
        return _last_or_none(df["k"])


class RocFactor(Factor):
    """Rate of Change: (close - close_n_ago) / close_n_ago * 100."""

    code = "roc12"
    name = "ROC变动率"
    category = "momentum"
    params = [FactorParam("period", 12, min=1, max=60, description="回看周期")]

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        period = self.default_kwargs()["period"]
        closes = _closes_up_to(db, stock, trade_date, n=period + 5)
        if len(closes) <= period:
            return None
        prev = closes.iloc[-period - 1]
        cur = closes.iloc[-1]
        if prev in (0, 0.0) or pd.isna(prev) or pd.isna(cur):
            return None
        return float((cur - prev) / prev * 100.0)


class CciFactor(Factor):
    """Commodity Channel Index (default 14)."""

    code = "cci14"
    name = "CCI商品通道"
    category = "momentum"
    params = [FactorParam("period", 14, min=5, max=50, description="CCI周期")]

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        period = self.default_kwargs()["period"]
        rows = market_service.get_kline(db, stock, end=trade_date)
        if not rows:
            return None
        r = [x for x in rows if x.close is not None]
        highs = pd.Series([float(x.high) for x in r], dtype=float).tail(_WINDOW)
        lows = pd.Series([float(x.low) for x in r], dtype=float).tail(_WINDOW)
        closes = pd.Series([float(x.close) for x in r], dtype=float).tail(_WINDOW)
        if len(closes) < period:
            return None
        tp = (highs + lows + closes) / 3.0
        ma_tp = tp.rolling(window=period, min_periods=period).mean()
        md = tp.rolling(window=period, min_periods=period).apply(
            lambda x: (x - x.mean()).abs().mean(), raw=True
        )
        cci = (tp - ma_tp) / (0.015 * md)
        return _last_or_none(cci)


for _p in (6, 12, 24):
    _f = RsiFactor()
    _f.code = f"rsi{_p}"
    _f.name = f"RSI{_p}相对强弱"
    _f.params = [FactorParam("period", _p, min=2, max=50, description="RSI周期")]
    registry.register(_f)

registry.register(KdjKFactor())
registry.register(RocFactor())
registry.register(CciFactor())
