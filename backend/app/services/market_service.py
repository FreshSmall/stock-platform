"""Market data service: stock search, info lookup, K-line fetch (Task B1).

These functions are the data-access layer between the FastAPI routers and the
read-only ``stock_pool`` / ``daily_prices`` tables. They take a SQLAlchemy
``Session`` and return ORM rows so the caller keeps control over the
transaction lifecycle (e.g. ``get_db`` in tests).

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

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.stock import DailyPrice, StockPool


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
) -> list[DailyPrice]:
    """Return daily OHLCV bars for ``code``, optionally bounded by date range.

    The result is ordered ascending by ``trade_date`` so it can be plotted
    left-to-right directly. Both bounds are inclusive.
    """
    stmt = select(DailyPrice).where(DailyPrice.stock_code == code)
    if start:
        stmt = stmt.where(DailyPrice.trade_date >= start)
    if end:
        stmt = stmt.where(DailyPrice.trade_date <= end)
    stmt = stmt.order_by(DailyPrice.trade_date.asc())
    return list(db.execute(stmt).scalars().all())
