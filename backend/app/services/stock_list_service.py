"""Stock list service (BP-V1.5-012).

Paginated browse/filter/sort over the latest ``stock_pool`` snapshot. This is
the "discover stocks" entry point — distinct from the V1 fuzzy ``search`` and
the fixed ``hot-stocks`` leaderboard. Industry comes from ``stock_pool`` (with
the V1.5 ``sa_stock_industry`` supplement as a fallback join, left for a later
task once that table is populated).

Only the freshest snapshot per ``stock_code`` participates, so a code never
appears twice even if ``stock_pool`` has multiple historical snapshots.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.stock import StockPool

_SORT_COL = {
    "pct_change": StockPool.pct_change,
    "amount": StockPool.total_mv,  # stock_pool has no per-day amount; use mv as proxy
    "total_mv": StockPool.total_mv,
    "pe": StockPool.pe,
}


def list_industries(db: Session) -> list[str]:
    """Distinct non-null industries from the latest pool snapshot."""
    latest = db.execute(select(func.max(StockPool.trade_date))).scalar()
    if latest is None:
        return []
    rows = db.execute(
        select(StockPool.industry)
        .where(StockPool.trade_date == latest, StockPool.industry.is_not(None))
        .distinct()
        .order_by(StockPool.industry)
    ).scalars().all()
    return [r for r in rows if r]


def list_stocks(
    db: Session,
    industry: str | None = None,
    tag: str | None = None,
    sort: str = "pct_change",
    order: str = "desc",
    page: int = 1,
    size: int = 20,
) -> dict:
    """Paginated stock list with optional industry filter and sort.

    :param tag: optional quick filter — ``limit_up`` / ``limit_down`` /
        ``top_gainers`` (pct_change >= 9.5 as a limit-up proxy for main board).
    :param sort: pct_change / amount / total_mv / pe.
    :return: ``{items, total, page, size}``.
    """
    latest = db.execute(select(func.max(StockPool.trade_date))).scalar()
    if latest is None:
        return {"items": [], "total": 0, "page": page, "size": size}

    base = select(StockPool).where(StockPool.trade_date == latest)
    if industry:
        base = base.where(StockPool.industry == industry)
    if tag == "limit_up":
        base = base.where(StockPool.pct_change >= 9.5)
    elif tag == "limit_down":
        base = base.where(StockPool.pct_change <= -9.5)
    elif tag == "top_gainers":
        base = base.where(StockPool.pct_change >= 5.0)

    # total count
    total = db.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar_one()

    sort_col = _SORT_COL.get(sort, StockPool.pct_change)
    order_col = sort_col.asc() if order == "asc" else sort_col.desc()
    rows = db.execute(
        base.order_by(order_col, StockPool.stock_code).offset((page - 1) * size).limit(size)
    ).scalars().all()

    items = [
        {
            "stock_code": r.stock_code,
            "stock_name": r.stock_name,
            "industry": r.industry,
            "close": float(r.close) if r.close is not None else None,
            "pct_change": float(r.pct_change) if r.pct_change is not None else None,
            "total_mv": float(r.total_mv) if r.total_mv is not None else None,
            "pe": float(r.pe) if r.pe is not None else None,
        }
        for r in rows
    ]
    return {"items": items, "total": int(total or 0), "page": page, "size": size}
