"""Sector (板块) router (BP-V1.5-005).

Endpoints under ``/api/v1/sector``.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.services import sector_service

router = APIRouter(prefix="/sector", tags=["sector"])


def _ok(data=None, msg: str = "ok") -> dict:
    from app.main import api_ok

    return api_ok(data, msg)


@router.get("")
def list_sectors(
    type: str = Query("industry", pattern="^(industry|concept)$"),
    sort: str = Query("pct_change", pattern="^(pct_change|amount|main_net_inflow|limit_up_count)$"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """Sector ranking for the latest day."""
    return _ok(sector_service.list_sectors(db, type, sort, limit))


@router.get("/{code}")
def detail(code: str, db: Session = Depends(get_db)) -> dict:
    """Sector definition + latest daily stats."""
    data = sector_service.get_sector_detail(db, code)
    if data is None:
        return _ok(None, msg="sector not found")
    return _ok(data)


@router.get("/{code}/stocks")
def stocks(
    code: str,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """Paginated constituent stocks of a sector."""
    return _ok(sector_service.list_sector_stocks(db, code, page, size))
