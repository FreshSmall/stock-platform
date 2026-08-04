"""Backtest router (Task C3): submit a run and poll its status/result.

Mounted under ``/api/v1/backtest``. V1 runs synchronously inside the request
(single-stock backtests finish in well under a second, see the perf check in
the task); V2 will hand the heavy lifting to Celery.

``_ok`` is imported lazily inside each handler to avoid the ``app.main``
circular import documented in :mod:`app.api.stock`.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id, get_db
from app.schemas.backtest import BacktestRequest
from app.services import backtest_service

router = APIRouter(prefix="/backtest", tags=["backtest"])


def _ok(data=None, msg: str = "ok") -> dict:
    """Build the unified success envelope (lazy import to avoid a cycle)."""
    from app.main import api_ok

    return api_ok(data, msg)


@router.post("")
def submit(
    req: BacktestRequest,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> dict:
    """Submit a backtest. V1 executes synchronously; returns run_id + status.

    A run that errors during execution is NOT raised to the client — the run
    row is marked ``failed`` and its ``error`` is surfaced via the GET
    endpoint. The POST still returns 200 so the client gets a ``run_id`` to
    poll.
    """
    run = backtest_service.create_backtest_run(db, user_id, req.model_dump())
    try:
        backtest_service.execute_and_store(db, run)
    except Exception:
        # status already marked failed + error stored; surface via GET.
        pass
    return _ok({"run_id": run.run_id, "status": run.status})


@router.get("/{run_id}")
def get_status(
    run_id: str,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> dict:
    """Poll a run's status and, once ``done``, its metrics/equity/trades (owner only)."""
    run = backtest_service.get_run(db, run_id)
    if run is None:
        return _ok(None, msg="run not found")
    if run.user_id is not None and run.user_id != user_id:
        # Don't leak existence of other users' runs.
        return _ok(None, msg="run not found")
    result = backtest_service.get_result(db, run_id)
    data = {
        "run_id": run.run_id,
        "status": run.status,
        "strategy": run.strategy,
        "params": run.params,
        "stock_pool": run.stock_pool,
        "start_date": run.start_date.isoformat() if run.start_date else None,
        "end_date": run.end_date.isoformat() if run.end_date else None,
    }
    if run.error:
        data["error"] = run.error
    if result:
        data["metrics"] = {
            "return_rate": result.return_rate,
            "max_drawdown": result.max_drawdown,
            "sharpe": result.sharpe,
            "win_rate": result.win_rate,
            # V2 advanced metrics
            "calmar": result.calmar,
            "information_ratio": result.information_ratio,
            "profit_loss_ratio": result.profit_loss_ratio,
            "benchmark_return": result.benchmark_return,
        }
        data["equity_curve"] = result.equity_curve
        data["benchmark_curve"] = result.benchmark_curve
        data["trades"] = result.trades
    return _ok(data)


@router.get("/{run_id}/drawdown")
def get_drawdown(
    run_id: str,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> dict:
    """The drawdown curve for a finished run (V2)."""
    run = backtest_service.get_run(db, run_id)
    if run is None or (run.user_id is not None and run.user_id != user_id):
        return _ok(None, msg="run not found")
    result = backtest_service.get_result(db, run_id)
    return _ok(result.drawdown_curve if result else None)


@router.get("/{run_id}/positions")
def get_positions(
    run_id: str,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> dict:
    """The position (holding size) curve for a finished run (V2)."""
    run = backtest_service.get_run(db, run_id)
    if run is None or (run.user_id is not None and run.user_id != user_id):
        return _ok(None, msg="run not found")
    result = backtest_service.get_result(db, run_id)
    return _ok(result.position_curve if result else None)
