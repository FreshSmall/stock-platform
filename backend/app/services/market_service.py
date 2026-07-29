"""Market data service: stock search/info/K-line (B1) + market overview (B3).

These functions are the data-access layer between the FastAPI routers and the
read-only ``stock_pool`` / ``daily_prices`` tables. They take a SQLAlchemy
``Session`` and return ORM rows (or plain dicts for the overview aggregates) so
the caller keeps control over the transaction lifecycle (e.g. ``get_db`` in
tests).

All queries rely on the existing MySQL indexes ``uk_code_date`` and
``idx_code``; single-stock date-range K-line queries are already sub-500ms on
the 1.1M-row ``daily_prices`` table, so we deliberately defer caching.

TODO(B1-perf): consider adding a 5-min response cache on the K-line endpoint if
profiling shows P95 creeping above 500ms. Caching a ``Session``-taking function
is awkward (Sessions are not hashable and must not be reused across requests),
so if/when we add caching it belongs at the API layer on the serialized
response, not here.
"""

from datetime import date

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.models.stock import DailyPrice, StockPool

# The three major A-share indices we surface on the overview screen.
# Stored as (code, display name). NOTE: as of Task B3 these codes are NOT
# present in ``daily_prices`` (which only holds individual stocks — e.g.
# ``000001`` there is 平安银行, not the 上证指数). ``get_indices`` therefore
# returns placeholder rows with ``close=None``/``pct_change=None``; real index
# quotes arrive via AkShare ingestion in Task B4.
#
# We deliberately do NOT filter index codes out of the breadth/hot-stock
# queries: ``000001`` collides with a real stock (平安银行), so a code-based
# exclusion would drop a legitimate stock from the counts. If B4 later stores
# indices in ``daily_prices`` it should use a distinct table or a prefixed code
# (e.g. ``sh000001``) to avoid this collision.
MAJOR_INDICES: list[tuple[str, str]] = [
    ("000001", "上证指数"),
    ("399001", "深证成指"),
    ("399006", "创业板指"),
]


def search_stocks(db: Session, q: str, limit: int = 20) -> list[StockPool]:
    """Search stocks by ``stock_code`` OR ``stock_name`` (fuzzy LIKE).

    ``stock_pool`` may contain multiple snapshots for the same code on
    different ``trade_date``s, so we over-fetch (sorted by most-recent date
    first) and then de-duplicate by ``stock_code`` to keep only the freshest
    snapshot per code.

    Args:
        db: an open SQLAlchemy session (caller manages its lifecycle).
        q: non-empty search string. Empty -> ``[]`` (defensive; the API layer
            also rejects empty ``q`` via ``Query(min_length=1)``).
        limit: max number of distinct codes to return.

    Returns:
        Up to ``limit`` :class:`StockPool` rows, freshest snapshot per code.
    """
    if not q:
        return []
    pattern = f"%{q}%"
    stmt = (
        select(StockPool)
        .where(
            or_(
                StockPool.stock_code.like(pattern),
                StockPool.stock_name.like(pattern),
            )
        )
        .order_by(StockPool.trade_date.desc(), StockPool.stock_code)
        # Over-fetch: worst case every row is the same code, so ``limit * 3``
        # gives de-dup headroom without unbounded scanning.
        .limit(limit * 3)
    )
    rows = db.execute(stmt).scalars().all()

    seen: set[str] = set()
    result: list[StockPool] = []
    for r in rows:
        if r.stock_code in seen:
            continue
        seen.add(r.stock_code)
        result.append(r)
        if len(result) >= limit:
            break
    return result


def get_stock_info(db: Session, code: str) -> StockPool | None:
    """Return the latest ``stock_pool`` row for ``code``, or ``None`` if unknown."""
    stmt = (
        select(StockPool)
        .where(StockPool.stock_code == code)
        .order_by(StockPool.trade_date.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def get_kline(
    db: Session,
    code: str,
    start: date | None = None,
    end: date | None = None,
    period: str = "d",
) -> list:
    """Return OHLCV bars for ``code``, optionally bounded by date range.

    ``period`` selects the bar size:

    - ``"d"`` (default): daily bars straight from ``daily_prices`` (returns
      :class:`DailyPrice` ORM rows).
    - ``"w"`` / ``"m"``: weekly / monthly bars aggregated from the daily bars
      via pandas ``resample`` (returns plain dicts with the same field names).
      Weekly bars are anchored on Friday (W-FRI); a week with no Friday bar
      still resolves to its last trading day. OHLC is open=first, high=max,
      low=min, close=last; volume/amount are summed.

    The result is ordered ascending by date so it can be plotted left-to-right.
    Both bounds are inclusive.
    """
    stmt = select(DailyPrice).where(DailyPrice.stock_code == code)
    if start:
        stmt = stmt.where(DailyPrice.trade_date >= start)
    if end:
        stmt = stmt.where(DailyPrice.trade_date <= end)
    stmt = stmt.order_by(DailyPrice.trade_date.asc())
    daily = list(db.execute(stmt).scalars().all())

    if period == "d" or not daily:
        return daily

    # Aggregate to weekly/monthly. Build a DataFrame keyed by trade_date.
    import pandas as pd

    df = pd.DataFrame(
        [
            {
                "trade_date": pd.Timestamp(r.trade_date),
                "open": float(r.open) if r.open is not None else None,
                "close": float(r.close) if r.close is not None else None,
                "high": float(r.high) if r.high is not None else None,
                "low": float(r.low) if r.low is not None else None,
                "volume": float(r.volume) if r.volume is not None else 0.0,
                "amount": float(r.amount) if r.amount is not None else 0.0,
                "pct_change": float(r.pct_change) if r.pct_change is not None else None,
                "turnover": float(r.turnover) if r.turnover is not None else None,
            }
            for r in daily
        ]
    )
    df = df.dropna(subset=["open", "close", "high", "low"]).set_index("trade_date").sort_index()

    rule = "W-FRI" if period == "w" else "ME"
    agg = df.resample(rule).agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "amount": "sum",
            "pct_change": "sum",
            "turnover": "sum",
        }
    ).dropna(subset=["open", "close"])

    # resample labels the bucket with the period END (Friday / month-end),
    # which may not be a real trading day. Re-label to the last actual trading
    # day in each bucket so the bar's date is a real session.
    last_dates = df.groupby(pd.Grouper(freq=rule)).apply(lambda g: g.index[-1] if not g.empty else None)
    agg.index = last_dates.reindex(agg.index).values
    agg = agg.dropna(subset=["open"])  # drop empty buckets

    # Emit plain dicts shaped like DailyPrice fields, but typed loosely so the
    # caller (K-line serializer) can treat daily/weekly uniformly.
    out: list[dict] = []
    for ts, row in agg.iterrows():
        out.append(
            {
                "trade_date": ts.date() if hasattr(ts, "date") else ts,
                "open": row["open"],
                "close": row["close"],
                "high": row["high"],
                "low": row["low"],
                "volume": int(row["volume"]) if row["volume"] == row["volume"] else None,
                "amount": row["amount"],
                "pct_change": row["pct_change"] if row["pct_change"] == row["pct_change"] else None,
                "turnover": row["turnover"] if row["turnover"] == row["turnover"] else None,
            }
        )
    return out


def get_indices(db: Session) -> list[dict]:
    """Return the latest quote for the 3 major A-share indices.

    A DB probe (Task B3) confirmed that none of the index codes (000001, 399001,
    399006) live in ``daily_prices`` — the ``000001`` that does appear there is
    平安银行, not 上证指数. So we emit placeholder rows with ``close=None`` /
    ``pct_change=None`` rather than returning misleading stock data.

    TODO(B4): replace this fallback with real quotes pulled from AkShare's
    index endpoint (e.g. ``ak.stock_zh_index_spot()``) once that ingestion
    pipeline is wired up. The contract (list of 3 dicts with code/name/close/
    pct_change) will stay the same so the router/tests need no changes.

    Returns:
        A list of 3 dicts, one per index, in the order 上证/深证/创业板.
    """
    return [
        {"code": code, "name": name, "close": None, "pct_change": None}
        for code, name in MAJOR_INDICES
    ]


def _latest_trade_date(db: Session) -> date | None:
    """Return the max ``trade_date`` in ``daily_prices``, or ``None`` if empty."""
    return db.execute(select(func.max(DailyPrice.trade_date))).scalar()


def get_market_summary(db: Session) -> dict:
    """Market breadth on the latest trading day in ``daily_prices``.

    Counts stocks by the sign of ``pct_change`` (advance > 0, decline < 0,
    flat == 0; rows with NULL pct_change are skipped) and sums ``amount`` across
    the whole market. Every ``daily_prices`` row counts as a stock today — see
    the note on :data:`MAJOR_INDICES` for why index codes are not filtered out.

    Returns:
        A dict shaped like :class:`app.schemas.market.MarketSummary`. If the
        table is empty, every count is 0 and ``trade_date``/``total_amount``
        are ``None``.
    """
    latest = _latest_trade_date(db)
    if latest is None:
        return {
            "trade_date": None,
            "advance_count": 0,
            "decline_count": 0,
            "flat_count": 0,
            "total_amount": None,
        }

    stmt = (
        select(
            func.coalesce(func.sum(DailyPrice.amount), 0).label("total_amount"),
        )
        .where(DailyPrice.trade_date == latest)
    )
    total_amount = db.execute(stmt).scalar_one()

    # Breadth counts: SUM(CASE WHEN sign ...) over the day's rows. We filter
    # NULL pct_change out of all three arms so the totals agree with the row
    # count a user would see in the table.
    sign_counts = db.execute(
        select(
            func.sum(
                case((DailyPrice.pct_change > 0, 1), else_=0)
            ).label("advance"),
            func.sum(
                case((DailyPrice.pct_change < 0, 1), else_=0)
            ).label("decline"),
            func.sum(
                case((DailyPrice.pct_change == 0, 1), else_=0)
            ).label("flat"),
        )
        .where(DailyPrice.trade_date == latest)
        .where(DailyPrice.pct_change.is_not(None))
    ).one()
    advance, decline, flat = sign_counts

    return {
        "trade_date": latest,
        "advance_count": int(advance or 0),
        "decline_count": int(decline or 0),
        "flat_count": int(flat or 0),
        "total_amount": total_amount,
    }


def get_hot_stocks(db: Session, sort: str = "amount", limit: int = 20) -> list[dict]:
    """Top stocks on the latest trading day, with names joined from stock_pool.

    ``sort="amount"`` ranks the most-active stocks by turnover desc;
    ``sort="pct_change"`` ranks top gainers by pct_change desc. Rows whose
    ``pct_change`` is NULL are dropped before ranking (a NULL pct_change can't
    meaningfully be "top") — for ``amount`` sort NULL amounts are also dropped
    so the leaderboard never shows a zero-turnover row at the top.

    ``stock_name`` comes from an OUTER JOIN against the freshest ``stock_pool``
    snapshot, because ``daily_prices`` is code+OHLCV only. A stock that isn't in
    the pool yet gets ``stock_name=None`` (the LEFT JOIN keeps the row).

    Args:
        db: an open SQLAlchemy session (caller manages its lifecycle).
        sort: one of ``"amount"`` / ``"pct_change"`` (validated at the API layer).
        limit: max rows to return (1..100, validated at the API layer).

    Returns:
        Up to ``limit`` dicts shaped like :class:`app.schemas.market.HotStock`.
    """
    latest = _latest_trade_date(db)
    if latest is None:
        return []

    sp_latest = db.execute(select(func.max(StockPool.trade_date))).scalar()

    stmt = (
        select(DailyPrice, StockPool.stock_name)
        .outerjoin(
            StockPool,
            and_(
                StockPool.stock_code == DailyPrice.stock_code,
                StockPool.trade_date == sp_latest,
            ),
        )
        .where(DailyPrice.trade_date == latest)
        .where(DailyPrice.pct_change.is_not(None))
    )
    if sort == "pct_change":
        stmt = stmt.order_by(DailyPrice.pct_change.desc())
    else:  # amount — also drop NULL amounts so the top isn't a zero row.
        stmt = stmt.where(DailyPrice.amount.is_not(None)).order_by(
            DailyPrice.amount.desc()
        )
    stmt = stmt.limit(limit)

    return [
        {
            "stock_code": dp.stock_code,
            "stock_name": name,
            "close": dp.close,
            "pct_change": dp.pct_change,
            "amount": dp.amount,
        }
        for dp, name in db.execute(stmt).all()
    ]
