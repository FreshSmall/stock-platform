"""Stock market router (Task B1): search, info, K-line.

All endpoints are mounted under ``/api/v1/stock`` (the ``/api/v1`` prefix comes
from the parent router in :mod:`app.main`). They return the unified envelope
via :func:`app.main.api_ok`.
"""

"""Stock market router (Task B1): search, info, K-line.

All endpoints are mounted under ``/api/v1/stock`` (the ``/api/v1`` prefix comes
from the parent router in :mod:`app.main`). They return the unified envelope
via :func:`app.main.api_ok`.

``api_ok`` is imported lazily inside each handler rather than at module top
level. The router is itself imported by ``app.main`` at startup, so a
top-level ``from app.main import api_ok`` would create a circular import
(partially-initialized ``app.main``); deferring it to call time sidesteps that
without polluting the public surface.
"""

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.schemas.stock import KLineItem, StockBrief, StockInfo
from app.services import market_service

router = APIRouter(prefix="/stock", tags=["stock"])


def _ok(data=None, msg: str = "ok") -> dict:
    """Build the unified success envelope (lazy import to avoid a cycle)."""
    from app.main import api_ok

    return api_ok(data, msg)


@router.get("/search")
def search(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """Fuzzy search stocks by code or name."""
    rows = market_service.search_stocks(db, q, limit)
    items = [
        StockBrief(
            stock_code=r.stock_code,
            stock_name=r.stock_name,
            exchange=r.exchange,
            industry=r.industry,
        )
        for r in rows
    ]
    return _ok(items)


@router.get("/{code}")
def get_info(code: str, db: Session = Depends(get_db)) -> dict:
    """Get the latest detailed snapshot for a single stock."""
    row = market_service.get_stock_info(db, code)
    if row is None:
        return _ok(None, msg="stock not found")
    return _ok(
        StockInfo(
            stock_code=row.stock_code,
            stock_name=row.stock_name,
            exchange=row.exchange,
            industry=row.industry,
            total_mv=row.total_mv,
            circ_mv=row.circ_mv,
            pe=row.pe,
            pb=row.pb,
            list_date=row.list_date,
            close=row.close,
            pct_change=row.pct_change,
        ).model_dump()
    )


@router.get("/{code}/kline")
def get_kline(
    code: str,
    start: date | None = None,
    end: date | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Get daily K-line bars for a stock, optionally bounded by date range."""
    rows = market_service.get_kline(db, code, start, end)
    items = [
        KLineItem(
            trade_date=r.trade_date,
            open=r.open,
            close=r.close,
            high=r.high,
            low=r.low,
            volume=r.volume,
            amount=r.amount,
            pct_change=r.pct_change,
            turnover=r.turnover,
        )
        for r in rows
    ]
    return _ok(items)
