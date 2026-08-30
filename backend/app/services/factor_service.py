"""Factor query/scoring service (BP-V2-001/004/013).

Wires the :mod:`factor` framework to the DB:
- compute a factor's value series for one stock over a date range;
- compute IC (effectiveness) for a factor across the universe;
- multi-factor weighted scoring → stock ranking.

IC computation pulls the latest stock_pool snapshot as the universe and uses
daily_prices for forward returns. Results are cached in sa_factor_ic so the
same (factor, date, horizon) isn't recomputed.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.factor import ic as ic_mod
from app.factor import registry
from app.models.factor import SaFactorIc
from app.models.stock import DailyPrice, StockPool

logger = logging.getLogger(__name__)

# Universe size for IC: top-N by amount keeps the computation fast (full market
# is 4000+; IC over ~300 most-liquid names is representative and < 10s).
_IC_UNIVERSE = 300


def list_factors(category: str | None = None) -> list[dict]:
    """All registered factors (optionally filtered by category)."""
    factors = registry.by_category(category) if category else registry.all_factors()
    return [
        {"code": f.code, "name": f.name, "category": f.category} for f in factors
    ]


def compute_series(
    db: Session, factor_code: str, stock: str, start: date, end: date
) -> list[dict]:
    """Factor value for one stock over [start, end], one point per trade day."""
    f = registry.get(factor_code)
    if f is None:
        return []
    rows = db.execute(
        select(DailyPrice.trade_date)
        .where(
            DailyPrice.stock_code == stock,
            DailyPrice.trade_date >= start,
            DailyPrice.trade_date <= end,
            DailyPrice.close.is_not(None),
        )
        .order_by(DailyPrice.trade_date.asc())
    ).scalars().all()
    return [
        {"trade_date": d.isoformat(), "value": f.compute(db, stock, d)}
        for d in rows
    ]


def _universe_codes(db: Session, trade_date: date) -> list[str]:
    """The IC universe: codes with valid close on/around trade_date."""
    latest = db.execute(
        select(func.max(DailyPrice.trade_date)).where(
            DailyPrice.trade_date <= trade_date
        )
    ).scalar()
    if latest is None:
        return []
    rows = db.execute(
        select(DailyPrice.stock_code)
        .where(
            DailyPrice.trade_date == latest,
            DailyPrice.close.is_not(None),
            DailyPrice.amount.is_not(None),
        )
        .order_by(DailyPrice.amount.desc())
        .limit(_IC_UNIVERSE)
    ).scalars().all()
    return list(rows)


def compute_ic(
    db: Session, factor_code: str, trade_date: date, horizon: int = 5
) -> dict | None:
    """IC / IR / layered returns for a factor on one rebalance date.

    For simplicity (and because full-panel IC over 4000 stocks × history is
    heavy), this computes a *single-date* IC: factor snapshot vs forward
    ``horizon``-day returns across the universe. ``ir`` and ``win_rate`` are
    derived from that snapshot's sign agreement.
    """
    f = registry.get(factor_code)
    if f is None:
        return None

    # The rebalance date may lack `horizon` forward trading days (the factor
    # page passes the range END as trade_date, which defaults to today).
    # Fall back to the latest settled date that HAS horizon forward days, so
    # the page works out of the box; the effective date is returned in the
    # payload. With fewer than horizon+1 settled dates in total there isn't
    # enough history for any date — the caller reports "insufficient data".
    settled_desc = db.execute(
        select(DailyPrice.trade_date)
        .where(DailyPrice.pct_change.is_not(None))
        .group_by(DailyPrice.trade_date)
        .order_by(DailyPrice.trade_date.desc())
        .limit(horizon + 1)
    ).scalars().all()
    if len(settled_desc) < horizon + 1:
        logger.warning("ic: not enough settled history (%d dates)", len(settled_desc))
        return None
    latest_ok = min(trade_date, settled_desc[-1])
    if latest_ok != trade_date:
        logger.info(
            "ic: trade_date %s lacks %d forward days, falling back to %s",
            trade_date, horizon, latest_ok,
        )
        trade_date = latest_ok

    codes = _universe_codes(db, trade_date)
    if len(codes) < 10:
        logger.warning("ic: universe too small (%d), skipping", len(codes))
        return None

    # Warm the per-session caches with bulk queries so the per-code compute
    # loop below stays off the DB — without this each code triggers its own
    # query (~30ms × 300 codes ≈ 10s, the dominant cost of this endpoint).
    from app.factor import cache as fcache
    from app.services import market_service

    if f.category in ("trend", "momentum", "volatility", "volume"):
        market_service.prefetch_kline_windows(db, codes, trade_date)
    if f.category in ("volume", "fundamental"):
        fcache.prefetch_stock_pool(db, codes, trade_date)
    if f.category == "fundamental":
        fcache.prefetch_financial(db, codes)
    if f.category == "sentiment":
        fcache.prefetch_market_tables(db)
        fcache.prefetch_streak(db, codes, trade_date)

    # factor values + forward-close on the universe
    fv, fwd_close = {}, {}
    for code in codes:
        v = f.compute(db, code, trade_date)
        if v is not None:
            fv[code] = v
    if len(fv) < 10:
        return None

    # forward close: the close `horizon` trade days after trade_date
    fwd_dates = db.execute(
        select(DailyPrice.trade_date)
        .where(DailyPrice.trade_date > trade_date, DailyPrice.close.is_not(None))
        .group_by(DailyPrice.trade_date)
        .order_by(DailyPrice.trade_date.asc())
        .limit(horizon + 1)
    ).scalars().all()
    if len(fwd_dates) < horizon:
        return None
    fwd_date = fwd_dates[horizon - 1]
    base_closes = {
        code: close
        for code, close in db.execute(
            select(DailyPrice.stock_code, DailyPrice.close).where(
                DailyPrice.trade_date == trade_date,
                DailyPrice.stock_code.in_(list(fv.keys())),
            )
        ).all()
        if close is not None
    }
    fwd_closes = {
        code: close
        for code, close in db.execute(
            select(DailyPrice.stock_code, DailyPrice.close).where(
                DailyPrice.trade_date == fwd_date,
                DailyPrice.stock_code.in_(list(fv.keys())),
            )
        ).all()
        if close is not None
    }
    fwd_ret = {
        code: float(fwd_closes[code]) / float(base_closes[code]) - 1.0
        for code in fv
        if code in base_closes and code in fwd_closes and base_closes[code]
    }
    fv_series = pd.Series({c: v for c, v in fv.items() if c in fwd_ret})
    fr_series = pd.Series(fwd_ret)
    if len(fv_series) < 10:
        return None

    ic_val = ic_mod.single_ic(fv_series, fr_series)
    layered = ic_mod.layered_returns(fv_series, fr_series)
    win_rate = float((fv_series * fr_series > 0).mean()) if len(fv_series) else None

    return {
        "factor_code": factor_code,
        "trade_date": trade_date.isoformat(),
        "horizon": horizon,
        "ic": round(ic_val, 4) if ic_val is not None else None,
        "win_rate": round(win_rate, 4) if win_rate is not None else None,
        "layered_returns": layered,
        "universe_size": len(fv_series),
    }


def multi_factor_score(
    db: Session,
    factors: list[dict],  # [{"code": "pe", "weight": 1.0}, ...]
    trade_date: date,
    universe_size: int = 200,
) -> list[dict]:
    """Weighted multi-factor score → ranked stock list (BP-V2-004).

    Each factor is z-score-normalised across the universe (so PE and RSI are
    comparable), then weighted-summed. Higher score = better (caller may want
    to flip sign for "lower is better" factors — left to the caller via weight
    sign).
    """
    codes = _universe_codes(db, trade_date)[:universe_size]
    if not codes:
        return []

    # gather factor matrix: code -> {factor_code: value}
    matrix: dict[str, dict[str, float]] = {c: {} for c in codes}
    for spec in factors:
        fc, w = spec["code"], spec.get("weight", 1.0)
        f = registry.get(fc)
        if f is None:
            continue
        for code in codes:
            v = f.compute(db, code, trade_date)
            if v is not None:
                matrix[code][fc] = v * w  # apply weight sign/magnitude

    # z-score per factor, then sum
    df = pd.DataFrame(matrix).T
    z = (df - df.mean()) / (df.std().replace(0, pd.NA))
    scores = z.sum(axis=1).dropna().sort_values(ascending=False)

    return [
        {"stock_code": code, "score": round(float(score), 4)}
        for code, score in scores.head(universe_size).items()
    ]
