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
from app.services import market_data_service, market_service, sentiment_service

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


@router.get("/sentiment")
def sentiment(db: Session = Depends(get_db)) -> dict:
    """Market sentiment rollup for the latest trading day (BP-V1.5-006/011).

    Returns the most recent persisted ``sa_market_sentiment`` row. If none has
    been computed yet (e.g. before the first scheduled run), returns ``None``.
    """
    from app.models.sentiment import SaMarketSentiment
    from sqlalchemy import select

    row = db.execute(
        select(SaMarketSentiment).order_by(SaMarketSentiment.trade_date.desc()).limit(1)
    ).scalar_one_or_none()
    if row is None:
        return _ok(None, msg="no sentiment data")
    return _ok(
        {
            "trade_date": row.trade_date,
            "limit_up_count": row.limit_up_count,
            "limit_down_count": row.limit_down_count,
            "failed_limit_count": row.failed_limit_count,
            "seal_rate": float(row.seal_rate) if row.seal_rate is not None else None,
            "max_streak": row.max_streak,
            "up_count": row.up_count,
            "down_count": row.down_count,
            "streak_ladder": row.streak_ladder,
        }
    )


@router.get("/north-flow")
def north_flow(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> dict:
    """Daily northbound net inflow (沪/深股通) for the recent ``days``."""
    return _ok(market_data_service.get_north_flow(db, days))
