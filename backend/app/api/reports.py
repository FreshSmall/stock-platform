"""Agent reports router (BP-V2-009~012). Endpoints under ``/api/v1/reports``."""

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id, get_db
from app.services import agent_service

router = APIRouter(prefix="/reports", tags=["reports"])


def _ok(data=None, msg: str = "ok") -> dict:
    from app.main import api_ok

    return api_ok(data, msg)


@router.get("")
def list_reports(
    agent: str | None = Query(None, pattern="^(sector|market|review|recommend)$"),
    trade_date: date | None = None,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _uid: int = Depends(get_current_user_id),
) -> dict:
    """List agent reports."""
    return _ok(agent_service.list_reports(db, agent, trade_date, limit))


@router.get("/{report_id}")
def get_report(
    report_id: int,
    db: Session = Depends(get_db),
    _uid: int = Depends(get_current_user_id),
) -> dict:
    data = agent_service.get_report(db, report_id)
    if data is None:
        return _ok(None, msg="report not found")
    return _ok(data)


@router.post("/{agent}/generate")
def generate(
    agent: str,
    target: str | None = None,
    db: Session = Depends(get_db),
    _uid: int = Depends(get_current_user_id),
) -> dict:
    """Manually trigger an agent run."""
    try:
        return _ok(agent_service.generate(db, agent, target))
    except ValueError as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=str(e))
