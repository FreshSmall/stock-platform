"""Dragon-tiger (龙虎榜) router (BP-V1.5-002).

Endpoints under ``/api/v1/dragon-tiger``.
"""

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.services import market_data_service

router = APIRouter(prefix="/dragon-tiger", tags=["dragon-tiger"])


def _ok(data=None, msg: str = "ok") -> dict:
    from app.main import api_ok

    return api_ok(data, msg)


@router.get("")
def list_board(
    trade_date: date | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """Dragon-tiger stock list for one day (latest if ``trade_date`` omitted)."""
    return _ok(market_data_service.list_dragon_tiger(db, trade_date))


@router.get("/{code}")
def history(code: str, db: Session = Depends(get_db)) -> dict:
    """All dragon-tiger listings for one stock."""
    return _ok(market_data_service.get_dragon_tiger_history(db, code))


@router.get("/{code}/{trade_date}/seats")
def seats(code: str, trade_date: date, db: Session = Depends(get_db)) -> dict:
    """Top-5 buy/sell seats for one stock/day."""
    return _ok(market_data_service.get_dragon_tiger_seats(db, code, trade_date))
