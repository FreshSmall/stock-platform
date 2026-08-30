"""Sentiment factors (BP-V2-001).

Read market-wide and per-stock sentiment data materialised by V1.5:
- market sentiment (涨停数/炸板率/封板率) from sa_market_sentiment
- northbound net inflow from sa_north_flow
- per-stock limit-up streak from sa_limit_up_streak

These are market-wide signals applied per stock (a hot market lifts all boats),
except the streak which is per-stock. Lookups go through the session-scoped
caches in :mod:`app.factor.cache` (N+1 fix — compute_ic evaluates the same
market date once per universe code, 300 identical queries otherwise).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.factor import cache as fcache
from app.factor.base import Factor, registry
from app.models.sentiment import SaLimitUpStreak, SaMarketSentiment


def _market_sentiment_on(db: Session, trade_date: date) -> SaMarketSentiment | None:
    return fcache.latest_le(fcache.market_sentiment_daily(db), trade_date)


class LimitUpCountFactor(Factor):
    """市场涨停家数（市场情绪温度计）."""

    code = "market_limit_up"
    name = "市场涨停家数"
    category = "sentiment"

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        s = _market_sentiment_on(db, trade_date)
        return float(s.limit_up_count) if s and s.limit_up_count is not None else None


class SealRateFactor(Factor):
    """市场封板率（越高情绪越强）."""

    code = "market_seal_rate"
    name = "市场封板率"
    category = "sentiment"

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        s = _market_sentiment_on(db, trade_date)
        return float(s.seal_rate) if s and s.seal_rate is not None else None


class MaxStreakFactor(Factor):
    """市场最高连板高度（题材活跃度）."""

    code = "market_max_streak"
    name = "市场最高连板"
    category = "sentiment"

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        s = _market_sentiment_on(db, trade_date)
        return float(s.max_streak) if s and s.max_streak is not None else None


class NorthFlowFactor(Factor):
    """北向资金当日净流入（沪+深合计，亿元）."""

    code = "north_net_inflow"
    name = "北向资金净流入"
    category = "sentiment"

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        row = fcache.latest_le(fcache.north_flow_daily(db), trade_date)
        return float(row.value) if row is not None and row.value is not None else None


class StockStreakFactor(Factor):
    """个股连板天数（per-stock sentiment）."""

    code = "stock_streak"
    name = "个股连板天数"
    category = "sentiment"

    def compute(self, db: Session, stock: str, trade_date: date) -> float | None:
        row = fcache.latest_le(fcache.streak_rows_for(db, stock), trade_date)
        if row is None or row.streak_days is None:
            return None
        return float(row.streak_days)


for _cls in (
    LimitUpCountFactor, SealRateFactor, MaxStreakFactor,
    NorthFlowFactor, StockStreakFactor,
):
    registry.register(_cls())
