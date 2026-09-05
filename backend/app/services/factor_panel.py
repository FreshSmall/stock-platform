"""Vectorized market / factor panels (V2.2 BP-V2.2-002 / T2.3).

One SQL round-trip builds date × stock panels of close/volume; factors are
then computed column-wise with rolling/ewm ops. This is the only feasible
route for full-market IC series and periodic-rebalance portfolio backtests —
the per-stock ``Factor.compute`` path does one DB query per code and cannot
cover thousands of codes × hundreds of dates.

Panel factor formulas mirror the registry definitions exactly for
rolling-window factors; ewm-based factors (rsi/ema/macd_dif) differ from the
registry only in warmup seeding (full-history recursion vs 120-bar re-seed) —
the difference decays to noise after the warmup window. Tests lock the two
paths together on real data.

Not panel-computable (use the single-date ``compute_ic`` path): high/low
derived factors (adx14/supertrend/kdj_k/cci14/atr14/boll_width), snapshot
factors (pe/pb/total_mv/roe/eps/*_growth/turnover), market-wide sentiment
factors.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.kline import SaDailyTradeStatus, SaStockLifecycle
from app.models.stock import DailyPrice

logger = logging.getLogger(__name__)

PANEL_FACTORS: frozenset[str] = frozenset(
    {
        # trend
        "ma5", "ma10", "ma20", "ema12", "ema26", "macd_dif",
        # momentum
        "roc5", "roc12", "roc20", "roc60", "roc120",
        "rsi6", "rsi12", "rsi14", "rsi24",
        # volatility
        "hv20", "skew20",
        # volume / liquidity
        "amt20", "vol_ratio5", "vol_ratio10", "obv_trend", "vol_price_trend",
    }
)

# Calendar-day buffer before `start` so long lookback factors (roc120) are
# warm at the first rebalance date (180 trading days ≈ 270 calendar days).
WARMUP_CALENDAR_DAYS = 300

# Trades-per-year scaling for holding-period returns (A-share convention).
TRADING_DAYS_PER_YEAR = 250


@dataclass
class MarketPanel:
    """date × stock panels of daily close / volume (raw values, NaN = no bar)."""

    close: pd.DataFrame
    volume: pd.DataFrame

    @property
    def dates(self) -> pd.DatetimeIndex:
        return self.close.index

    @property
    def codes(self) -> list[str]:
        return list(self.close.columns)


def load_market_panel(
    db: Session,
    start: date,
    end: date,
    codes: list[str] | None = None,
) -> MarketPanel:
    """Build the close/volume panel for ``[start - warmup, end]``.

    ``end`` is the DATA end — callers asking for forward returns must pass an
    already-extended end. Rows arrive as plain tuples (no ORM overhead) and
    pivot into date × code frames.
    """
    stmt = (
        select(
            DailyPrice.stock_code,
            DailyPrice.trade_date,
            DailyPrice.close,
            DailyPrice.volume,
        )
        .where(
            DailyPrice.trade_date >= start - timedelta(days=WARMUP_CALENDAR_DAYS),
            DailyPrice.trade_date <= end,
            DailyPrice.close.is_not(None),
        )
        .order_by(DailyPrice.trade_date.asc())
    )
    if codes:
        stmt = stmt.where(DailyPrice.stock_code.in_(codes))
    rows = db.execute(stmt).all()
    if not rows:
        return MarketPanel(close=pd.DataFrame(), volume=pd.DataFrame())

    frame = pd.DataFrame(
        rows, columns=["code", "d", "close", "volume"]
    )
    close = frame.pivot(index="d", columns="code", values="close").sort_index()
    close.index = pd.to_datetime(close.index)  # datetime.date → DatetimeIndex
    volume = (
        frame.pivot(index="d", columns="code", values="volume").sort_index()
        .reindex(index=close.index, columns=close.columns)
    )
    close = close.astype(float)
    volume = volume.astype(float)
    return MarketPanel(close=close, volume=volume)


def select_universe(
    db: Session,
    panel: MarketPanel,
    start: date,
    end: date,
    pool: str = "current",
    universe_size: int | None = None,
) -> list[str]:
    """Liquidity-ranked universe from the panel (optionally PIT-restricted).

    Ranking metric: mean daily traded amount (close×volume) over the whole
    range — less recency-biased than a trailing window and stable for
    delisted-in-range names. ``pool="pit"`` restricts candidates to stocks
    listed at ``end`` plus those delisted within the range (both remain
    subject to per-date listing masking downstream).
    """
    amount = (panel.close * panel.volume).mean()
    amount = amount.dropna().sort_values(ascending=False)

    candidates = list(amount.index)
    if pool == "pit":
        rows = db.execute(
            select(SaStockLifecycle.stock_code, SaStockLifecycle.delist_date)
        ).all()
        listed_at_end: set[str] = set()
        delisted_in_range: set[str] = set()
        for code, delist in rows:
            if delist is None:
                listed_at_end.add(code)
            elif start <= delist <= end:
                delisted_in_range.add(code)
        pit = listed_at_end | delisted_in_range
        candidates = [c for c in candidates if c in pit]
        if not candidates:  # lifecycle empty → fall back to all-with-data
            candidates = list(amount.index)

    if universe_size:
        candidates = candidates[:universe_size]
    return candidates


def pit_listed_mask(
    db: Session, panel: MarketPanel, codes: list[str]
) -> pd.DataFrame:
    """date × code boolean mask: stock was listed and not delisted that day.

    Stocks without a lifecycle row fall back to the panel's own first bar as
    the listing date (same convention as ``universe_service.get_pool_asof``).
    """
    rows = db.execute(
        select(
            SaStockLifecycle.stock_code,
            SaStockLifecycle.list_date,
            SaStockLifecycle.delist_date,
        )
    ).all()
    lifecycle = {code: (lst, delist) for code, lst, delist in rows}

    dates = panel.dates
    listed = pd.DataFrame(True, index=dates, columns=codes)
    first_bar = panel.close.apply(lambda s: s.first_valid_index())
    for code in codes:
        lst, delist = lifecycle.get(code, (None, None))
        start_bound = lst or first_bar.get(code)
        if start_bound is not None:
            start_bound = pd.Timestamp(start_bound)
        mask = pd.Series(True, index=dates)
        if start_bound is not None:
            mask &= dates >= start_bound
        if delist is not None:
            mask &= dates <= pd.Timestamp(delist)
        listed[code] = mask.to_numpy()
    return listed


@dataclass
class TradeStatusPanels:
    """Per-date tradability masks aligned to a panel's dates/codes."""

    is_st: pd.DataFrame | None
    is_suspended: pd.DataFrame | None
    not_tradable: pd.DataFrame | None  # buy_tradable=0 OR sell_tradable=0


def load_trade_status(
    db: Session, panel: MarketPanel, codes: list[str]
) -> TradeStatusPanels:
    """Load sa_daily_trade_status into boolean panels (None when no rows)."""
    rows = db.execute(
        select(
            SaDailyTradeStatus.stock_code,
            SaDailyTradeStatus.trade_date,
            SaDailyTradeStatus.is_st,
            SaDailyTradeStatus.is_suspended,
            SaDailyTradeStatus.buy_tradable,
            SaDailyTradeStatus.sell_tradable,
        ).where(SaDailyTradeStatus.stock_code.in_(codes))
    ).all()
    if not rows:
        return TradeStatusPanels(None, None, None)
    frame = pd.DataFrame(
        rows,
        columns=["code", "d", "is_st", "is_suspended", "buy", "sell"],
    )
    dates, cols = panel.dates, codes

    is_st = frame.pivot(index="d", columns="code", values="is_st").reindex(
        index=dates, columns=cols
    )
    is_st = (is_st == 1)  # NaN (no row) → False: unknown is not "known ST"
    suspended = (
        frame.pivot(index="d", columns="code", values="is_suspended")
        .reindex(index=dates, columns=cols)
        == 1
    )
    buy = frame.pivot(index="d", columns="code", values="buy").reindex(
        index=dates, columns=cols
    )
    sell = frame.pivot(index="d", columns="code", values="sell").reindex(
        index=dates, columns=cols
    )
    # no status row → tradable (unknown is not a restriction); 0 → blocked
    not_tradable = (buy == 0) | (sell == 0)
    return TradeStatusPanels(is_st=is_st, is_suspended=suspended, not_tradable=not_tradable)


def panel_factor_values(panel: MarketPanel, code: str) -> pd.DataFrame:
    """date × code values for a panel factor; ValueError for unsupported."""
    close, vol = panel.close, panel.volume

    if code in ("ma5", "ma10", "ma20"):
        return close.rolling(int(code[2:]), min_periods=int(code[2:])).mean()
    if code in ("ema12", "ema26"):
        return close.ewm(span=int(code[3:]), adjust=False).mean()
    if code == "macd_dif":
        fast = close.ewm(span=12, adjust=False).mean()
        slow = close.ewm(span=26, adjust=False).mean()
        return fast - slow
    if code.startswith("roc"):
        n = int(code[3:])
        return (close / close.shift(n) - 1.0) * 100.0
    if code.startswith("rsi"):
        p = int(code[3:])
        delta = close.diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)
        avg_gain = gain.ewm(alpha=1.0 / p, adjust=False, min_periods=p).mean()
        avg_loss = loss.ewm(alpha=1.0 / p, adjust=False, min_periods=p).mean()
        rs = avg_gain / avg_loss
        rsi = 100.0 - 100.0 / (1.0 + rs)
        return rsi.where(avg_loss != 0, 100.0)
    if code == "hv20":
        logret = np.log(close / close.shift(1))
        return logret.rolling(20, min_periods=20).std() * math.sqrt(250)
    if code == "skew20":
        logret = np.log(close / close.shift(1))
        return logret.rolling(20, min_periods=20).skew()
    if code == "amt20":
        return (close * vol).rolling(20, min_periods=20).mean()
    if code.startswith("vol_ratio"):
        n = int(code[9:])
        base = vol.shift(1).rolling(n, min_periods=n).mean()
        return vol / base.where(base > 0)
    if code == "obv_trend":
        direction = np.sign(close.diff())
        obv = (direction * vol).cumsum()
        return np.sign(obv - obv.shift(5))
    if code == "vol_price_trend":
        price_up = close > close.shift(5)
        vol_up = vol > vol.shift(5)
        return (price_up & vol_up).astype(float) - (~price_up & ~vol_up).astype(
            float
        )
    raise ValueError(
        f"factor {code!r} is not panel-computable; supported: {sorted(PANEL_FACTORS)}"
    )


def forward_return_panel(close: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """``close[t+h] / close[t] - 1`` per date × code (NaN beyond data end)."""
    return close.shift(-horizon) / close - 1.0
