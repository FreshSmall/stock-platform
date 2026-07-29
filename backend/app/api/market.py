"""Market overview router (Task B3): indices, market breadth, hot stocks.

All endpoints are mounted under ``/api/v1/market`` (the ``/api/v1`` prefix comes
from the parent router in :mod:`app.main`). They return the unified envelope
via :func:`app.main.api_ok`.

``api_ok`` is imported lazily inside ``_ok`` rather than at module top level.
The router is itself imported by ``app.main`` at startup, so a top-level
``from app.main import api_ok`` would create a circular import (partially
initialized ``app.main``); deferring it to call time sidesteps that without
polluting the public surface — same pattern as :mod:`app.api.stock`.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.schemas.market import HotStock, IndexQuote, MarketSummary
from app.services import market_service

router = APIRouter(prefix="/market", tags=["market"])


def _ok(data=None, msg: str = "ok") -> dict:
    """Build the unified success envelope (lazy import to avoid a cycle)."""
    from app.main import api_ok

    return api_ok(data, msg)


@router.get("/indices")
def indices(db: Session = Depends(get_db)) -> dict:
    """Latest quote for the 3 major A-share indices.

    Until AkShare index ingestion (Task B4) lands, ``close``/``pct_change``
    come back as ``None`` — see :func:`app.services.market_service.get_indices`.
    """
    items = [IndexQuote(**r).model_dump() for r in market_service.get_indices(db)]
    return _ok(items)


@router.get("/summary")
def summary(db: Session = Depends(get_db)) -> dict:
    """Market breadth on the latest trading day (advance/decline/flat + total)."""
    data = MarketSummary(**market_service.get_market_summary(db)).model_dump()
    return _ok(data)


@router.get("/hot-stocks")
def hot_stocks(
    sort: str = Query("amount", pattern="^(amount|pct_change)$"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """Top stocks on the latest trading day.

    ``sort=amount`` -> most active (turnover desc); ``sort=pct_change`` ->
    top gainers (pct_change desc). ``stock_name`` is joined from the latest
    ``stock_pool`` snapshot.
    """
    items = [
        HotStock(**r).model_dump()
        for r in market_service.get_hot_stocks(db, sort, limit)
    ]
    return _ok(items)
