"""Sector (板块) read service (BP-V1.5-005).

Queries the V1.5 ``sa_sector`` / ``sa_sector_daily`` / ``sa_sector_stock``
tables. Sector ranking reads the latest ``sa_sector_daily`` snapshot;
constituent stocks join ``stock_pool`` for names/prices.

All functions return plain dicts ready for the API serializer.
"""

from typing import Optional

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models.sector import SaSector, SaSectorDaily, SaSectorStock
from app.models.stock import StockPool


def _latest_sector_date(db: Session, sector_type: str) -> Optional[object]:
    return db.execute(
        select(func.max(SaSectorDaily.trade_date)).where(
            SaSectorDaily.sector_type == sector_type
        )
    ).scalar()


def list_sectors(
    db: Session,
    sector_type: str = "industry",
    sort: str = "pct_change",
    limit: int = 50,
) -> list[dict]:
    """Sector ranking for the latest day, sorted by ``sort``.

    :param sort: one of pct_change / amount / main_net_inflow / limit_up_count.
    :return: list of dicts.

    When ``sa_sector_daily`` has no ingested data (e.g. the push2 sector
    endpoint is unreachable), fall back to aggregating ``stock_pool`` by
    industry for ``sector_type='industry'`` — so the page is never blank.
    Concept sectors have no fallback (stock_pool has no concept tag).
    """
    latest = _latest_sector_date(db, sector_type)
    if latest is None:
        # Fallback: aggregate stock_pool by industry (industry type only).
        if sector_type == "industry":
            return _list_sectors_from_pool(db, sort, limit)
        return []
    sort_col = {
        "amount": SaSectorDaily.amount,
        "main_net_inflow": SaSectorDaily.main_net_inflow,
        "limit_up_count": SaSectorDaily.limit_up_count,
    }.get(sort, SaSectorDaily.pct_change)
    rows = db.execute(
        select(SaSectorDaily, SaSector.sector_name)
        .join(
            SaSector,
            (SaSector.sector_code == SaSectorDaily.sector_code)
            & (SaSector.sector_type == SaSectorDaily.sector_type),
        )
        .where(SaSectorDaily.trade_date == latest, SaSectorDaily.sector_type == sector_type)
        .order_by(desc(sort_col))
        .limit(limit)
    ).all()
    return [
        {
            "sector_code": d.sector_code,
            "sector_name": name,
            "trade_date": d.trade_date,
            "pct_change": float(d.pct_change) if d.pct_change is not None else None,
            "amount": float(d.amount) if d.amount is not None else None,
            "limit_up_count": d.limit_up_count,
            "main_net_inflow": float(d.main_net_inflow) if d.main_net_inflow is not None else None,
            "leader_code": d.leader_code,
        }
        for d, name in rows
    ]


def _list_sectors_from_pool(db: Session, sort: str, limit: int) -> list[dict]:
    """Fallback sector ranking: aggregate the latest stock_pool by industry.

    Computes per-industry avg pct_change, sum amount, member count and a
    limit-up proxy (pct_change >= 9.5). ``sector_code`` is the industry name
    itself (URL-safe enough for the detail route, which tolerates unknown
    codes by returning an empty member list).
    """
    from sqlalchemy import case, func as _f

    from app.models.stock import StockPool

    latest = db.execute(select(_f.max(StockPool.trade_date))).scalar()
    if latest is None:
        return []
    avg_pct = _f.avg(StockPool.pct_change)
    sum_mv = _f.sum(StockPool.total_mv)
    cnt = _f.count(StockPool.stock_code)
    lim_up = _f.sum(case((StockPool.pct_change >= 9.5, 1), else_=0))
    order_expr = {
        "amount": sum_mv,
        "limit_up_count": lim_up,
        "main_net_inflow": sum_mv,  # no inflow in pool; proxy by mv
    }.get(sort, avg_pct)
    rows = db.execute(
        select(
            StockPool.industry.label("name"),
            avg_pct.label("pct"),
            sum_mv.label("mv"),
            cnt.label("cnt"),
            lim_up.label("lim"),
        )
        .where(StockPool.trade_date == latest, StockPool.industry.is_not(None))
        .group_by(StockPool.industry)
        .order_by(desc(order_expr))
        .limit(limit)
    ).all()
    return [
        {
            "sector_code": name,
            "sector_name": name,
            "trade_date": latest,
            "pct_change": round(float(pct), 2) if pct is not None else None,
            "amount": float(mv) if mv is not None else None,
            "limit_up_count": int(lim) if lim is not None else 0,
            "main_net_inflow": None,
            "leader_code": None,
            "member_count": int(cnt),
        }
        for name, pct, mv, cnt, lim in rows
    ]


def get_sector_detail(db: Session, sector_code: str) -> dict | None:
    """Sector definition + latest daily stats."""
    sec = db.execute(
        select(SaSector).where(SaSector.sector_code == sector_code).limit(1)
    ).scalar_one_or_none()
    if sec is None:
        return None
    latest = db.execute(
        select(SaSectorDaily)
        .where(SaSectorDaily.sector_code == sector_code, SaSectorDaily.sector_type == sec.sector_type)
        .order_by(desc(SaSectorDaily.trade_date))
        .limit(1)
    ).scalar_one_or_none()
    return {
        "sector_code": sec.sector_code,
        "sector_name": sec.sector_name,
        "sector_type": sec.sector_type,
        "trade_date": latest.trade_date if latest else None,
        "pct_change": float(latest.pct_change) if latest and latest.pct_change is not None else None,
        "amount": float(latest.amount) if latest and latest.amount is not None else None,
        "limit_up_count": latest.limit_up_count if latest else None,
        "main_net_inflow": float(latest.main_net_inflow) if latest and latest.main_net_inflow is not None else None,
        "leader_code": latest.leader_code if latest else None,
    }


def list_sector_stocks(
    db: Session, sector_code: str, page: int = 1, size: int = 20
) -> dict:
    """Paginated constituent stocks of a sector, joined with latest pool snapshot.

    Stocks without a pool snapshot still appear (LEFT JOIN) with null name/price.
    """
    total = db.execute(
        select(func.count())
        .select_from(SaSectorStock)
        .where(SaSectorStock.sector_code == sector_code)
    ).scalar_one()
    sp_latest = db.execute(select(func.max(StockPool.trade_date))).scalar()
    rows = db.execute(
        select(SaSectorStock.stock_code, StockPool.stock_name, StockPool.close, StockPool.pct_change)
        .outerjoin(
            StockPool,
            (StockPool.stock_code == SaSectorStock.stock_code)
            & (StockPool.trade_date == sp_latest),
        )
        .where(SaSectorStock.sector_code == sector_code)
        .order_by(desc(StockPool.pct_change))
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    items = [
        {
            "stock_code": code,
            "stock_name": name,
            "close": float(close) if close is not None else None,
            "pct_change": float(pct) if pct is not None else None,
        }
        for code, name, close, pct in rows
    ]
    return {"items": items, "total": int(total or 0), "page": page, "size": size}
