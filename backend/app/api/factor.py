"""Factor router (BP-V2-001/004/013). Endpoints under ``/api/v1/factor``."""

from datetime import date

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.services import factor_service

router = APIRouter(prefix="/factor", tags=["factor"])


def _ok(data=None, msg: str = "ok") -> dict:
    from app.main import api_ok

    return api_ok(data, msg)


@router.get("")
def list_factors(
    category: str | None = Query(None, pattern="^(trend|momentum|volatility|volume|fundamental|sentiment)$"),
) -> dict:
    """List all factors, optionally filtered by category."""
    return _ok(factor_service.list_factors(category))


@router.get("/{code}/compute")
def compute_series(
    code: str,
    stock: str = Query(...),
    start: date | None = None,
    end: date | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Factor value series for one stock over a date range."""
    if end is None:
        end = date.today()
    if start is None:
        start = end.replace(year=end.year - 1)
    return _ok(factor_service.compute_series(db, code, stock, start, end))


@router.get("/{code}/ic")
def compute_ic(
    code: str,
    horizon: int = Query(5, ge=1, le=60),
    trade_date: date | None = None,
    pool: str = Query("current", pattern="^(current|pit)$"),
    exclude_st: bool = Query(False),
    exclude_suspended: bool = Query(False),
    only_tradable: bool = Query(False),
    db: Session = Depends(get_db),
) -> dict:
    """IC analysis for a factor on one rebalance date (latest if omitted).

    V2.1 sample governance: ``pool=pit`` uses the point-in-time universe;
    the three flags drop ST / suspended / unbuyable codes (defaults = V2 behaviour).
    """
    if trade_date is None:
        trade_date = date.today()
    data = factor_service.compute_ic(
        db, code, trade_date, horizon,
        pool=pool,
        exclude_st=exclude_st,
        exclude_suspended=exclude_suspended,
        only_tradable=only_tradable,
    )
    if data is None:
        return _ok(None, msg="insufficient data")
    return _ok(data)


@router.post("/score")
def multi_factor_score(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
) -> dict:
    """Multi-factor weighted scoring → ranked stock list (BP-V2-004).

    Body: ``{"factors": [{"code": "pe", "weight": 1.0}, ...], "trade_date": "2026-07-28",
    "pool": "current|pit", "exclude_st": false, "exclude_suspended": false,
    "only_tradable": false}`` — the sample-governance keys are optional and
    default to the unchanged V2 behaviour.
    """
    factors = payload.get("factors", [])
    td = payload.get("trade_date")
    trade_date = date.fromisoformat(td) if td else date.today()
    if not factors:
        return _ok(None, msg="no factors specified")
    return _ok(
        factor_service.multi_factor_score(
            db, factors, trade_date,
            pool=payload.get("pool", "current"),
            exclude_st=bool(payload.get("exclude_st", False)),
            exclude_suspended=bool(payload.get("exclude_suspended", False)),
            only_tradable=bool(payload.get("only_tradable", False)),
        )
    )
