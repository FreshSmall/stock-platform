"""Market sentiment computation (V1.5 BP-V1.5-006 / 011).

Derives the daily market sentiment rollup (limit-up/down counts, failed-limit
count, seal rate, max streak, limit-up ladder) from the read-only
``daily_prices`` table, and persists it to ``sa_market_sentiment`` +
``sa_limit_up_streak``.

Limit-up rule (A-share convention):
  - 涨停阈值 by board: 主板 10%, 创业板/科创板 20%, ST 5%.
  - Board inferred from code prefix: 300/688 -> 20%, else 10%.
  - ST inferred from stock_name containing "ST".
  - 涨停价 = round(prev_close * (1 + threshold), 2) (rounded to fen).
  - 涨停: close >= 涨停价.  炸板: high >= 涨停价 but close < 涨停价.
  - 跌停同理 (close <= 跌停价).

This is the most intricate business logic in V1.5; it is heavily unit-tested
against synthetic cases (see tests/test_sentiment_service.py).
"""

import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.sentiment import SaLimitUpStreak, SaMarketSentiment
from app.models.stock import DailyPrice, StockPool

logger = logging.getLogger(__name__)

# Board limit-up thresholds (as fractions). ST overrides to 0.05.
_THRESHOLD_MAIN = Decimal("0.10")
_THRESHOLD_KCB = Decimal("0.20")  # 创业板 300 + 科创板 688


def limit_threshold(stock_code: str, stock_name: str | None) -> Decimal:
    """Return the limit-up/down threshold for a stock.

    创业板 (300xxx) and 科创板 (688xxx) use ±20%; everything else ±10%.
    ST stocks (name contains "ST") use ±5% regardless of board.
    """
    if stock_name and "ST" in stock_name:
        return Decimal("0.05")
    if stock_code.startswith(("300", "688")):
        return _THRESHOLD_KCB
    return _THRESHOLD_MAIN


def limit_prices(prev_close: Decimal, threshold: Decimal) -> tuple[Decimal, Decimal]:
    """Return ``(limit_up_price, limit_down_price)`` rounded to 2dp (fen).

    Rounded half-up so it matches exchange rounding on a fen grid.
    """
    up = (prev_close * (Decimal(1) + threshold)).quantize(Decimal("0.01"))
    down = (prev_close * (Decimal(1) - threshold)).quantize(Decimal("0.01"))
    return up, down


def classify(
    prev_close: Decimal | None,
    close: Decimal | None,
    high: Decimal | None,
    threshold: Decimal,
) -> str:
    """Classify a bar into one of: limit_up / limit_down / failed_limit / normal.

    - ``limit_up``: close reached the up limit (sealed).
    - ``limit_down``: close reached the down limit.
    - ``failed_limit``: intraday high touched the up limit but close failed to
      seal it (炸板).
    - ``normal``: otherwise.
    Returns ``"normal"`` when prev_close/close/high are missing.
    """
    if prev_close is None or close is None:
        return "normal"
    up, down = limit_prices(prev_close, threshold)
    if close <= down:
        return "limit_down"
    if close >= up:
        return "limit_up"
    if high is not None and high >= up:
        return "failed_limit"
    return "normal"


def _prev_trade_date(db: Session, trade_date: date) -> date | None:
    """The most recent trade_date strictly before ``trade_date``."""
    return db.execute(
        select(func.max(DailyPrice.trade_date)).where(
            DailyPrice.trade_date < trade_date
        )
    ).scalar()


def _name_map(db: Session, trade_date: date) -> dict[str, str]:
    """stock_code -> stock_name from the latest stock_pool snapshot <= trade_date.

    stock_name is needed to detect ST. We use the freshest pool on or before the
    trade date so the classification reflects the name as of that day.
    """
    latest_pool = db.execute(
        select(func.max(StockPool.trade_date)).where(
            StockPool.trade_date <= trade_date
        )
    ).scalar()
    if latest_pool is None:
        return {}
    rows = db.execute(
        select(StockPool.stock_code, StockPool.stock_name).where(
            StockPool.trade_date == latest_pool
        )
    ).all()
    return {code: name for code, name in rows if code}


def compute_streak(db: Session, trade_date: date) -> dict[str, int]:
    """Compute per-stock consecutive limit-up streaks for ``trade_date``.

    A stock's streak = (prev-day streak + 1) if it hit limit-up today, else 0.
    Upserts results into ``sa_limit_up_streak`` and returns ``{code: streak}``.

    Only stocks that traded today AND have a prior close (for limit calc) are
    considered.
    """
    prev_date = _prev_trade_date(db, trade_date)
    if prev_date is None:
        return {}

    # prev_close per code
    prev_closes = {
        code: close
        for code, close in db.execute(
            select(DailyPrice.stock_code, DailyPrice.close).where(
                DailyPrice.trade_date == prev_date,
                DailyPrice.close.is_not(None),
            )
        ).all()
    }
    # prev streak per code
    prev_streaks = {
        code: streak
        for code, streak in db.execute(
            select(SaLimitUpStreak.stock_code, SaLimitUpStreak.streak_days).where(
                SaLimitUpStreak.trade_date == prev_date
            )
        ).all()
    }
    names = _name_map(db, trade_date)

    today_rows = db.execute(
        select(
            DailyPrice.stock_code,
            DailyPrice.close,
            DailyPrice.high,
        )
        .where(DailyPrice.trade_date == trade_date)
        .where(DailyPrice.close.is_not(None))
    ).all()

    streaks: dict[str, int] = {}
    payload = []
    for code, close, high in today_rows:
        prev_close = prev_closes.get(code)
        if prev_close is None:
            continue
        threshold = limit_threshold(code, names.get(code))
        kind = classify(prev_close, close, high, threshold)
        streak = (prev_streaks.get(code, 0) + 1) if kind == "limit_up" else 0
        streaks[code] = streak
        payload.append(
            {
                "stock_code": code,
                "trade_date": trade_date,
                "streak_days": streak,
            }
        )

    if payload:
        from sqlalchemy.dialects.mysql import insert as mysql_insert

        stmt = mysql_insert(SaLimitUpStreak).values(payload)
        stmt = stmt.on_duplicate_key_update(
            {"streak_days": stmt.inserted.streak_days}
        )
        db.execute(stmt)
        db.commit()
    return streaks


def compute_sentiment(db: Session, trade_date: date) -> dict | None:
    """Compute the full sentiment rollup for ``trade_date`` and persist it.

    Returns the sentiment dict (shaped like SaMarketSentiment) or None if there
    is no data for that day. Idempotent: re-running overwrites the row.
    """
    prev_date = _prev_trade_date(db, trade_date)
    if prev_date is None:
        return None

    prev_closes = {
        code: close
        for code, close in db.execute(
            select(DailyPrice.stock_code, DailyPrice.close).where(
                DailyPrice.trade_date == prev_date,
                DailyPrice.close.is_not(None),
            )
        ).all()
    }
    names = _name_map(db, trade_date)

    today_rows = db.execute(
        select(
            DailyPrice.stock_code,
            DailyPrice.close,
            DailyPrice.high,
            DailyPrice.pct_change,
        )
        .where(DailyPrice.trade_date == trade_date)
        .where(DailyPrice.close.is_not(None))
    ).all()

    if not today_rows:
        return None

    counts = {"limit_up": 0, "limit_down": 0, "failed_limit": 0}
    up_count = 0
    down_count = 0
    for code, close, high, pct in today_rows:
        prev_close = prev_closes.get(code)
        kind = (
            classify(prev_close, close, high, limit_threshold(code, names.get(code)))
            if prev_close is not None
            else "normal"
        )
        if kind in counts:
            counts[kind] += 1
        if pct is not None:
            if pct > 0:
                up_count += 1
            elif pct < 0:
                down_count += 1

    streaks = compute_streak(db, trade_date)
    # ladder: streak_days -> count (only streak >= 1)
    ladder: dict[int, int] = defaultdict(int)
    for s in streaks.values():
        if s >= 1:
            ladder[s] += 1
    max_streak = max(ladder.keys()) if ladder else 0

    sealed = counts["limit_up"]
    attempted = sealed + counts["failed_limit"]
    seal_rate = (Decimal(sealed) / Decimal(attempted)) if attempted else None

    data = {
        "trade_date": trade_date,
        "limit_up_count": sealed,
        "limit_down_count": counts["limit_down"],
        "failed_limit_count": counts["failed_limit"],
        "seal_rate": seal_rate,
        "max_streak": max_streak,
        "up_count": up_count,
        "down_count": down_count,
        "streak_ladder": {str(k): v for k, v in sorted(ladder.items())} or None,
    }

    from sqlalchemy.dialects.mysql import insert as mysql_insert

    stmt = mysql_insert(SaMarketSentiment).values(data)
    stmt = stmt.on_duplicate_key_update(
        {
            c: getattr(stmt.inserted, c)
            for c in (
                "limit_up_count",
                "limit_down_count",
                "failed_limit_count",
                "seal_rate",
                "max_streak",
                "up_count",
                "down_count",
                "streak_ladder",
            )
        }
    )
    db.execute(stmt)
    db.commit()
    return data
