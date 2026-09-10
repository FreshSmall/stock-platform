"""Paper trading router (V3a / BP-V3a). Endpoints under ``/api/v1/paper``."""

from datetime import date

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id, get_db
from app.services import paper_service

router = APIRouter(prefix="/paper", tags=["paper"])


def _ok(data=None, msg: str = "ok") -> dict:
    from app.main import api_ok

    return api_ok(data, msg)


@router.post("/accounts")
def create_account(
    body: dict = Body(...),
    db: Session = Depends(get_db),
    _uid: int = Depends(get_current_user_id),
) -> dict:
    """Create a paper account (config defaults see paper_service.DEFAULT_CONFIG)."""
    return _ok(paper_service.create_account(db, body["name"], body.get("config")))


@router.get("/accounts")
def list_accounts(
    db: Session = Depends(get_db),
    _uid: int = Depends(get_current_user_id),
) -> dict:
    return _ok({"accounts": paper_service.list_accounts(db)})


@router.get("/accounts/{account_id}")
def get_account(
    account_id: int,
    db: Session = Depends(get_db),
    _uid: int = Depends(get_current_user_id),
) -> dict:
    return _ok(paper_service.get_account(db, account_id))


@router.post("/accounts/{account_id}/status")
def set_status(
    account_id: int,
    body: dict = Body(...),
    db: Session = Depends(get_db),
    _uid: int = Depends(get_current_user_id),
) -> dict:
    return _ok(paper_service.set_status(db, account_id, body["action"]))


@router.get("/accounts/{account_id}/nav")
def nav_history(
    account_id: int,
    start: date | None = Query(None),
    end: date = Query(...),
    db: Session = Depends(get_db),
    _uid: int = Depends(get_current_user_id),
) -> dict:
    return _ok(paper_service.nav_history(db, account_id, start, end))


@router.get("/accounts/{account_id}/orders")
def orders_history(
    account_id: int,
    start: date | None = Query(None),
    end: date | None = Query(None),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
    _uid: int = Depends(get_current_user_id),
) -> dict:
    return _ok(paper_service.orders_history(db, account_id, start, end, status))


@router.get("/accounts/{account_id}/drift")
def run_drift(
    account_id: int,
    start: date = Query(...),
    end: date = Query(...),
    db: Session = Depends(get_db),
    _uid: int = Depends(get_current_user_id),
) -> dict:
    """Replay the account config as a portfolio backtest and diff NAV paths."""
    return _ok(paper_service.run_drift(db, account_id, start, end))


@router.post("/accounts/{account_id}/tick")
def tick_account(
    account_id: int,
    body: dict = Body(default=None),
    db: Session = Depends(get_db),
    _uid: int = Depends(get_current_user_id),
) -> dict:
    """Manually advance one account (catch-up replay; ≤ 20 days per call)."""
    dates = [date.fromisoformat(x) for x in (body or {}).get("dates") or []] or None
    if dates and len(dates) > 20:
        return _ok(None, msg="单次补跑最多 20 个交易日")
    results = [paper_service.paper_tick(db, account_id, d) for d in (dates or [date.today()])]
    return _ok({"results": results})
