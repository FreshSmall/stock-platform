"""Portfolio router (BP-V2-005). Endpoints under ``/api/v1/portfolio``."""

from datetime import date

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id, get_db
from app.services import portfolio_service

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


def _ok(data=None, msg: str = "ok") -> dict:
    from app.main import api_ok

    return api_ok(data, msg)


@router.get("")
def list_portfolios(
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> dict:
    """List the caller's portfolios."""
    return _ok(portfolio_service.list_portfolios(db, user_id))


@router.post("")
def create_portfolio(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> dict:
    """Create a portfolio. Body: ``{"name","holdings":[{"stock_code","weight"}]}``."""
    name = payload.get("name", "")
    holdings = payload.get("holdings", [])
    if not name or not holdings:
        return _ok(None, msg="name and holdings required")
    data = portfolio_service.create_portfolio(
        db,
        name=name,
        holdings=holdings,
        user_id=user_id,
        description=payload.get("description"),
        benchmark=payload.get("benchmark", "sh000001"),
    )
    return _ok(data)


@router.get("/{portfolio_id}")
def get_portfolio(
    portfolio_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> dict:
    data = portfolio_service.get_portfolio(db, portfolio_id)
    if data is None:
        return _ok(None, msg="portfolio not found")
    return _ok(data)


@router.put("/{portfolio_id}")
def update_portfolio(
    portfolio_id: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> dict:
    data = portfolio_service.update_portfolio(
        db,
        portfolio_id,
        name=payload.get("name"),
        description=payload.get("description"),
        benchmark=payload.get("benchmark"),
        holdings=payload.get("holdings"),
    )
    if data is None:
        return _ok(None, msg="portfolio not found")
    return _ok(data)


@router.delete("/{portfolio_id}")
def delete_portfolio(
    portfolio_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> dict:
    ok = portfolio_service.delete_portfolio(db, portfolio_id)
    return _ok({"deleted": ok})


@router.post("/{portfolio_id}/backtest")
def portfolio_nav(
    portfolio_id: int,
    payload: dict = Body(default={}),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> dict:
    """Weighted buy-and-hold NAV of the portfolio over a date range."""
    start = payload.get("start")
    end = payload.get("end")
    start_d = date.fromisoformat(start) if start else None
    end_d = date.fromisoformat(end) if end else None
    data = portfolio_service.portfolio_nav(db, portfolio_id, start_d, end_d)
    if data is None:
        return _ok(None, msg="portfolio not found or empty")
    return _ok(data)
