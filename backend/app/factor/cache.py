"""Session-scoped caches for factor data access (N+1 fix, part 2).

Technical factors already run on :func:`market_service.get_kline`'s per-session
kline cache. These helpers cover the remaining per-(code, date) lookups that
used to issue one query per factor compute — stock_pool valuation snapshots
(pe/pb/total_mv/turnover), sa_financial_extra (roe/eps/growth),
sa_limit_up_streak, and the market-wide sentiment tables. Same principle:
within one request the tables cannot meaningfully change, so load once per
code (or once for a whole small table) and serve every lookup from memory.

The ``compute_series`` loop (same code, ~240 dates) hits the per-code cache;
``compute_ic`` (300 codes × one date) warms the caches with bulk prefetches
dispatched by factor category in :mod:`app.services.factor_service`.
"""

from __future__ import annotations

import bisect
from collections import namedtuple
from datetime import date
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import SaFinancialExtra
from app.models.market_data import SaNorthFlow
from app.models.sentiment import SaLimitUpStreak, SaMarketSentiment
from app.models.stock import StockPool

_ALL = "__all__"


def _ns_cache(db: Session, ns: str) -> dict:
    attr = f"_factor_cache_{ns}"
    cache = getattr(db, attr, None)
    if cache is None:
        cache = {}
        setattr(db, attr, cache)
    return cache


def per_code_rows(
    db: Session, ns: str, code: str, loader: Callable[[str], list]
) -> list:
    """All rows for ``code`` (ascending by date), cached per session.

    ``loader(code)`` runs the DB query on first access; a code with no rows
    caches an empty list so repeat lookups stay off the DB too.
    """
    cache = _ns_cache(db, ns)
    rows = cache.get(code)
    if rows is None:
        rows = loader(code)
        cache[code] = rows
    return rows


def whole_table(db: Session, ns: str, loader: Callable[[], list]) -> list:
    """A (small) table loaded once per session — for market-wide series."""
    cache = _ns_cache(db, ns)
    rows = cache.get(_ALL)
    if rows is None:
        rows = loader()
        cache[_ALL] = rows
    return rows


def latest_le(rows: list, trade_date: date, date_attr: str = "trade_date"):
    """The last row whose date is ≤ ``trade_date`` (rows ascending), or None."""
    if not rows:
        return None
    dates = [getattr(r, date_attr) for r in rows]
    idx = bisect.bisect_right(dates, trade_date)
    return rows[idx - 1] if idx > 0 else None


def warm(db: Session, ns: str, grouped: dict[str, list]) -> None:
    """Bulk-seed a per-code cache from ``{code: rows_ascending}``."""
    _ns_cache(db, ns).update(grouped)


# --- stock_pool: valuation / turnover snapshots ------------------------------

_POOL_COLS = (
    StockPool.stock_code,
    StockPool.trade_date,
    StockPool.pe,
    StockPool.pb,
    StockPool.total_mv,
    StockPool.turnover,
)


def pool_rows_for(db: Session, code: str) -> list:
    """Cached stock_pool snapshot rows (ascending) for one code."""
    return per_code_rows(
        db,
        "stock_pool",
        code,
        lambda c: db.execute(
            select(*_POOL_COLS)
            .where(StockPool.stock_code == c)
            .order_by(StockPool.trade_date.asc())
        )
        .all(),
    )


def _group_and_seed(
    rows: list, codes: list[str], code_attr: str = "stock_code"
) -> dict[str, list]:
    """Group rows by code AND seed empty lists for absent codes.

    Negative caching matters: e.g. ``sa_financial_extra`` is empty until the
    finance sync populates it — without seeding, every one of compute_ic's
    ~300 codes would fall back to its own query (~26ms each ≈ 8s).
    """
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(getattr(r, code_attr), []).append(r)
    for c in codes:
        grouped.setdefault(c, [])
    return grouped


def prefetch_stock_pool(db: Session, codes: list[str], end: date) -> None:
    """Warm the stock_pool cache for many codes with one windowed query."""
    if not codes:
        return
    rows = (
        db.execute(
            select(*_POOL_COLS)
            .where(StockPool.stock_code.in_(codes), StockPool.trade_date <= end)
            .order_by(StockPool.stock_code.asc(), StockPool.trade_date.asc())
        )
        .all()
    )
    warm(db, "stock_pool", _group_and_seed(rows, codes))


# --- sa_financial_extra: per-report fundamentals ------------------------------

_FIN_COLS = (
    SaFinancialExtra.stock_code,
    SaFinancialExtra.report_date,
    SaFinancialExtra.roe,
    SaFinancialExtra.eps,
    SaFinancialExtra.revenue_growth,
    SaFinancialExtra.profit_growth,
)


def fin_rows_for(db: Session, code: str) -> list:
    """Cached sa_financial_extra rows (ascending by report_date) for one code."""
    return per_code_rows(
        db,
        "fin_extra",
        code,
        lambda c: db.execute(
            select(*_FIN_COLS)
            .where(SaFinancialExtra.stock_code == c)
            .order_by(SaFinancialExtra.report_date.asc())
        )
        .all(),
    )


def prefetch_financial(db: Session, codes: list[str]) -> None:
    if not codes:
        return
    rows = (
        db.execute(
            select(*_FIN_COLS)
            .where(SaFinancialExtra.stock_code.in_(codes))
            .order_by(
                SaFinancialExtra.stock_code.asc(),
                SaFinancialExtra.report_date.asc(),
            )
        )
        .all()
    )
    warm(db, "fin_extra", _group_and_seed(rows, codes))


# --- market sentiment / north flow / limit-up streak --------------------------

# North-flow derived row: one (trade_date, summed net_buy over channels).
DailyFlow = namedtuple("DailyFlow", ["trade_date", "value"])


def market_sentiment_daily(db: Session) -> list:
    """Cached sa_market_sentiment rows, ascending."""
    return whole_table(
        db,
        "market_sentiment",
        lambda: db.execute(
            select(SaMarketSentiment).order_by(SaMarketSentiment.trade_date.asc())
        )
        .scalars()
        .all(),
    )


def north_flow_daily(db: Session) -> list:
    """Cached [(trade_date, summed net_buy)] ascending — one row per date."""
    from sqlalchemy import func

    return whole_table(
        db,
        "north_flow_daily",
        lambda: [
            DailyFlow(d, float(v) if v is not None else None)
            for d, v in db.execute(
                select(SaNorthFlow.trade_date, func.sum(SaNorthFlow.net_buy))
                .group_by(SaNorthFlow.trade_date)
                .order_by(SaNorthFlow.trade_date.asc())
            )
        ],
    )


def streak_rows_for(db: Session, code: str) -> list:
    """Cached sa_limit_up_streak rows (ascending) for one code."""
    return per_code_rows(
        db,
        "limit_up_streak",
        code,
        lambda c: db.execute(
            select(
                SaLimitUpStreak.stock_code,
                SaLimitUpStreak.trade_date,
                SaLimitUpStreak.streak_days,
            )
            .where(SaLimitUpStreak.stock_code == c)
            .order_by(SaLimitUpStreak.trade_date.asc())
        )
        .all(),
    )


def prefetch_streak(db: Session, codes: list[str], end: date) -> None:
    if not codes:
        return
    rows = (
        db.execute(
            select(
                SaLimitUpStreak.stock_code,
                SaLimitUpStreak.trade_date,
                SaLimitUpStreak.streak_days,
            )
            .where(
                SaLimitUpStreak.stock_code.in_(codes),
                SaLimitUpStreak.trade_date <= end,
            )
            .order_by(
                SaLimitUpStreak.stock_code.asc(),
                SaLimitUpStreak.trade_date.asc(),
            )
        )
        .all()
    )
    warm(db, "limit_up_streak", _group_and_seed(rows, codes))


def prefetch_market_tables(db: Session) -> None:
    """Warm the two market-wide sentiment caches (tiny tables)."""
    market_sentiment_daily(db)
    north_flow_daily(db)
