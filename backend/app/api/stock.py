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

import pandas as pd
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.schemas.stock import KLineItem, StockBrief, StockInfo
from app.services import indicator_service, market_service

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


@router.get("/{code}/indicators")
def get_indicators(
    code: str,
    type: str = Query(..., pattern="^(ma|macd|kdj)$"),
    start: date | None = None,
    end: date | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Compute a technical indicator series over the K-line window.

    ``type`` selects the indicator: ``ma`` (5/10/20), ``macd`` (12/26/9) or
    ``kdj`` (9). The K-line rows come from :func:`market_service.get_kline`;
    only bars whose close (and, for KDJ, high/low) are non-null participate,
    and indicator values are emitted NaN-as-null so the front end can skip
    warmup bars without special handling.
    """
    rows = market_service.get_kline(db, code, start, end)
    if not rows:
        return _ok(None, msg="no data")

    closes = pd.Series([float(r.close) for r in rows if r.close is not None])
    dates = [r.trade_date.isoformat() for r in rows if r.close is not None]

    if type == "ma":
        df = indicator_service.calc_ma(closes)
    elif type == "macd":
        df = indicator_service.calc_macd(closes)
    else:  # kdj — high/low required, keep index aligned with closes.
        highs = pd.Series([float(r.high) for r in rows if r.close is not None])
        lows = pd.Series([float(r.low) for r in rows if r.close is not None])
        df = indicator_service.calc_kdj(highs, lows, closes)

    data = [
        {"trade_date": d, **{k: (None if pd.isna(v) else float(v)) for k, v in row.items()}}
        for d, row in zip(dates, df.to_dict(orient="records"))
    ]
    return _ok(data)
