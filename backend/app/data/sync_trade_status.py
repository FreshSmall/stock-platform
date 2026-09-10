"""Daily trade-status computation (V2.1 BP-V2.1-005).

Derives ST / suspension / limit-board tradability flags per stock/day from
data already in the house:

* **limit board & one-word boards** — pure arithmetic on raw OHLC vs the
  previous close (board threshold by code prefix; halved for ST). Backfills
  the ENTIRE history, no extra fetches.
* **suspension** — in the day's ``stock_pool`` snapshot but no bar that day.
* **ST** — historical snapshot names containing "ST" (coverage starts when
  the platform's daily snapshots do; older years stay NULL and the filter
  surfaces coverage instead of guessing).

``buy_tradable=0`` on suspension or a sealed one-word limit-up (can't buy
in); ``sell_tradable=0`` on suspension or a sealed one-word limit-down
(can't sell out). The backtest matching layer consumes exactly these.
"""

import logging
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.models.kline import SaDailyTradeStatus
from app.models.stock import StockPool
from app.services.market_service import _kline_model

logger = logging.getLogger(__name__)

# Price comparison tolerance (2dp price grid).
_EPS = 1e-6


def base_threshold(code: str) -> float:
    """Limit-board threshold by code prefix (创业板/科创 20%, 北交 30%, else 10%)."""
    if code[:3] in ("300", "301", "302") or code[:3] in ("688", "689"):
        return 0.20
    if code[:2] in ("43", "83", "87", "88") or code[:2] == "92":
        return 0.30
    return 0.10


def classify_limit(
    prev_close: float | None,
    o: float | None,
    h: float | None,
    l: float | None,
    c: float | None,
    threshold: float,
) -> tuple[str, bool, bool]:
    """Classify one bar's limit status.

    Returns ``(limit_status, buy_tradable, sell_tradable)``. Limit prices are
    the exchange convention: previous close × (1 ± threshold), rounded to
    the 0.01 price grid.
    """
    if prev_close is None or c is None:
        return "none", True, True
    up_price = round(prev_close * (1 + threshold), 2)
    down_price = round(prev_close * (1 - threshold), 2)
    sealed_up = float(c) >= up_price - _EPS
    sealed_down = float(c) <= down_price + _EPS
    one_word = (
        o is not None and h is not None and l is not None
        and abs(float(o) - float(h)) < _EPS
        and abs(float(h) - float(l)) < _EPS
        and abs(float(l) - float(c)) < _EPS
    )
    if sealed_up and one_word:
        return "limit_up_one_word", False, True
    if sealed_down and one_word:
        return "limit_down_one_word", True, False
    if sealed_up:
        return "limit_up", True, True
    if sealed_down:
        return "limit_down", True, True
    return "none", True, True


def _st_codes_asof(db: Session, asof: date) -> set[str]:
    """Codes whose name contained "ST" in the latest snapshot ≤ ``asof``."""
    snap_date = db.execute(
        select(func.max(StockPool.trade_date)).where(StockPool.trade_date <= asof)
    ).scalar()
    if snap_date is None:
        return set()
    names = db.execute(
        select(StockPool.stock_code, StockPool.stock_name).where(
            StockPool.trade_date == snap_date
        )
    ).all()
    return {code for code, name in names if name and "ST" in str(name).upper()}


def _pool_codes_on(db: Session, on: date) -> set[str]:
    snap = db.execute(
        select(StockPool.stock_code).where(StockPool.trade_date == on)
    ).scalars()
    return set(snap)


def _upsert_status(db: Session, rows: list[dict]) -> int:
    if not rows:
        return 0
    stmt = mysql_insert(SaDailyTradeStatus).values(rows)
    update_cols = {
        c: getattr(stmt.inserted, c)
        for c in ("is_st", "is_suspended", "limit_status", "buy_tradable", "sell_tradable")
    }
    stmt = stmt.on_duplicate_key_update(update_cols)
    db.execute(stmt)
    db.commit()
    return len(rows)


def compute_trade_status(
    db: Session, trade_date: date | None = None, with_st: bool = True
) -> dict:
    """Compute & persist trade status for one trade date (default: latest).

    :return: summary ``{"date": d, "rows": n, "st_coverage": float}``.
    """
    model = _kline_model()
    if trade_date is None:
        trade_date = db.execute(
            select(func.max(model.trade_date))
        ).scalar()
        if trade_date is None:
            return {"date": None, "rows": 0, "st_coverage": 0.0}

    # Bars of the day + the previous bar per code (window covers halts).
    since = trade_date - timedelta(days=20)
    frame = pd.DataFrame(
        db.execute(
            select(
                model.stock_code,
                model.trade_date,
                model.open,
                model.close,
                model.high,
                model.low,
            ).where(
                model.trade_date >= since,
                model.trade_date <= trade_date,
            )
            .order_by(model.stock_code, model.trade_date.asc())
        ).all(),
        columns=["stock_code", "trade_date", "open", "close", "high", "low"],
    )
    if frame.empty:
        return {"date": trade_date, "rows": 0, "st_coverage": 0.0}

    st_codes = _st_codes_asof(db, trade_date) if with_st else set()

    day = frame[frame["trade_date"] == trade_date]
    prev = frame[frame["trade_date"] < trade_date].groupby("stock_code")["close"].last()

    rows: list[dict] = []
    st_flagged = 0
    for _, bar in day.iterrows():
        code = bar["stock_code"]
        if bar["close"] is None:
            continue
        prev_close = prev.get(code)
        prev_close = float(prev_close) if prev_close is not None else None
        is_st = 1 if code in st_codes else (0 if st_codes else None)
        if st_codes:
            st_flagged += int(bool(is_st))
        thr = base_threshold(code) / (2 if is_st else 1)
        limit_status, buyable, sellable = classify_limit(
            prev_close,
            None if bar["open"] is None else float(bar["open"]),
            None if bar["high"] is None else float(bar["high"]),
            None if bar["low"] is None else float(bar["low"]),
            float(bar["close"]),
            thr,
        )
        rows.append(
            {
                "stock_code": code,
                "trade_date": trade_date,
                "is_st": is_st,
                "is_suspended": 0,
                "limit_status": limit_status,
                "buy_tradable": int(buyable),
                "sell_tradable": int(sellable),
            }
        )

    # Suspensions: in that day's pool snapshot but no bar.
    pool_codes = _pool_codes_on(db, trade_date)
    bar_codes = set(day["stock_code"])
    for code in sorted(pool_codes - bar_codes):
        rows.append(
            {
                "stock_code": code,
                "trade_date": trade_date,
                "is_st": 1 if code in st_codes else (0 if st_codes else None),
                "is_suspended": 1,
                "limit_status": "none",
                "buy_tradable": 0,
                "sell_tradable": 0,
            }
        )

    n = _upsert_status(db, rows)
    coverage = (len(st_codes) / len(bar_codes)) if bar_codes else 0.0
    return {"date": trade_date, "rows": n, "st_coverage": round(coverage, 4)}


def backfill_trade_status(db: Session, start: date | None = None) -> dict:
    """Backfill every stored trade date from ``start`` (default: 5y window).

    Long task (admin/async). Processes one date at a time; each date is
    idempotent, so an interrupted run resumes where it stopped.
    """
    model = _kline_model()
    stmt = select(func.max(model.trade_date))
    if start is None:
        from app.core.config import settings

        start = date.today() - timedelta(days=365 * settings.history_years)
    dates = [
        d
        for (d,) in db.execute(
            select(model.trade_date)
            .where(model.trade_date >= start)
            .distinct()
            .order_by(model.trade_date.asc())
        ).all()
    ]
    total_rows = 0
    for i, d in enumerate(dates):
        summary = compute_trade_status(db, trade_date=d)
        total_rows += summary.get("rows", 0)
        if (i + 1) % 50 == 0:
            logger.info("trade status backfill: %d/%d dates, %d rows", i + 1, len(dates), total_rows)
    return {"dates": len(dates), "rows": total_rows}


def status_coverage(db: Session, trade_date: date) -> float:
    """Fraction of the day's bars that have a trade-status row (patrol input)."""
    model = _kline_model()
    bars = db.execute(
        select(func.count()).select_from(model).where(model.trade_date == trade_date)
    ).scalar() or 0
    if not bars:
        return 0.0
    flagged = db.execute(
        select(func.count()).select_from(SaDailyTradeStatus).where(
            SaDailyTradeStatus.trade_date == trade_date
        )
    ).scalar() or 0
    return min(flagged / bars, 1.0)
