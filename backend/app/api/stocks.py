"""Stock list router (BP-V1.5-012) — paginated browse/filter/sort.

Endpoints under ``/api/v1/stocks``.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.services import stock_list_service

router = APIRouter(prefix="/stocks", tags=["stocks"])


def _ok(data=None, msg: str = "ok") -> dict:
    from app.main import api_ok

    return api_ok(data, msg)


@router.get("")
def list_stocks(
    industry: str | None = Query(None),
    tag: str | None = Query(
        None, pattern="^(limit_up|limit_down|top_gainers|low_price|high_turnover)$"
    ),
    sort: str = Query("pct_change", pattern="^(pct_change|amount|total_mv|pe|price|turnover)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """Paginated stock list with optional industry filter and sort."""
    return _ok(stock_list_service.list_stocks(db, industry, tag, sort, order, page, size))


@router.get("/industries")
def industries(db: Session = Depends(get_db)) -> dict:
    """Distinct industries for the filter dropdown."""
    return _ok(stock_list_service.list_industries(db))
