"""Admin service (BP-V1.5-010): data-source status, task runs, user management.

Task runs map a logical task name to its sync function (registered below) and
record every execution in ``sa_admin_task_log`` so the admin console can show
history and allow manual re-runs.
"""

import logging
from datetime import datetime
from typing import Callable

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.market_data import SaAdminTaskLog
from app.models.user import SaUser

logger = logging.getLogger(__name__)

# Logical task name -> no-arg runner that performs the sync against its own
# session. Runners are registered lazily to avoid importing the (network-bound)
# akshare client at module import time.
_TASK_RUNNERS: dict[str, Callable[[], int]] = {}


def _register_runners() -> None:
    """Populate the task registry on first use (avoids import-time side effects)."""
    if _TASK_RUNNERS:
        return

    def _wrap(name: str, fn):
        def runner() -> int:
            db = SessionLocal()
            try:
                return fn(db)
            finally:
                db.close()

        _TASK_RUNNERS[name] = runner

    from app.data import (
        sync_daily,
        sync_dragon_tiger,
        sync_industry,
        sync_minute,
        sync_money_flow_detail,
        sync_north_flow,
        sync_sector,
    )
    from app.services import sentiment_service
    from datetime import date as _date

    _wrap("daily_k_sync", lambda db: _daily_k(db))
    _wrap("minute_k_sync", lambda db: sync_minute.sync_one_stock(db, "600519", period=5))
    _wrap("dragon_tiger_sync", lambda db: sync_dragon_tiger.sync_date(db, _date.today().strftime("%Y%m%d")))
    _wrap("north_flow_sync", lambda db: sync_north_flow.sync_all(db))
    _wrap("money_flow_detail_sync", lambda db: sync_money_flow_detail.sync_one_stock(db, "600519"))
    _wrap("sector_sync", lambda db: sync_sector.sync_all(db))
    _wrap("industry_sync", lambda db: sync_industry.sync_one_stock(db, "600519"))
    _wrap("sentiment_sync", lambda db: _sentiment(db))


def _daily_k(db) -> int:
    # delegate to the existing scheduler entry; returns row count best-effort
    from app.scheduler import run_daily_sync

    run_daily_sync()
    return 0


def _sentiment(db) -> int:
    from sqlalchemy import func as _f

    from app.models.stock import DailyPrice

    # Pick the latest date with settled bars (non-NULL pct_change); a bare max
    # would pick the current in-progress session whose rows have NULL pct_change
    # and yield an all-zero sentiment rollup.
    latest = db.execute(
        select(_f.max(DailyPrice.trade_date)).where(
            DailyPrice.pct_change.is_not(None)
        )
    ).scalar()
    if latest is None:
        return 0
    return 1 if sentiment_service.compute_sentiment(db, latest) is not None else 0


def list_tasks() -> list[dict]:
    """Static task catalog with the latest run status for each."""
    _register_runners()
    db = SessionLocal()
    try:
        out = []
        for name in _TASK_RUNNERS:
            last = db.execute(
                select(SaAdminTaskLog)
                .where(SaAdminTaskLog.task_name == name)
                .order_by(desc(SaAdminTaskLog.started_at))
                .limit(1)
            ).scalar_one_or_none()
            out.append(
                {
                    "task_name": name,
                    "last_status": last.status if last else None,
                    "last_started_at": last.started_at if last else None,
                    "last_finished_at": last.finished_at if last else None,
                    "last_rows": last.rows_affected if last else None,
                }
            )
        return out
    finally:
        db.close()


# Wall-clock cap for a whole task run (seconds). A task may loop over many
# stocks; cap the total so a stuck run can't block the scheduler thread or an
# admin HTTP request indefinitely. Individual akshare fetches are already
# bounded by akshare_client._with_timeout; this is the outer backstop.
_TASK_DEADLINE_SEC: float = 300.0


def _run_with_deadline(runner, task_name: str):
    """Run a task runner under a wall-clock deadline.

    Uses a worker thread + ``future.result(timeout=...)``. On timeout raises
    :class:`TimeoutError`; the (possibly still-running) worker thread is
    abandoned, same trade-off as akshare_client._with_timeout.
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

    # Dedicated single-worker pool per call so a stuck task can't starve others.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"task-{task_name}") as ex:
        fut = ex.submit(runner)
        try:
            return fut.result(timeout=_TASK_DEADLINE_SEC)
        except FuturesTimeout:
            raise TimeoutError(
                f"task {task_name} exceeded {_TASK_DEADLINE_SEC}s"
            )


def run_task(task_name: str, triggered_by: str) -> dict:
    """Execute a task synchronously and log the outcome.

    :return: the created ``sa_admin_task_log`` row as a dict.
    """
    _register_runners()
    runner = _TASK_RUNNERS.get(task_name)
    if runner is None:
        raise ValueError(f"unknown task: {task_name}")

    started = datetime.now()
    log = SaAdminTaskLog(
        task_name=task_name, started_at=started, status="running", triggered_by=triggered_by
    )
    db = SessionLocal()
    try:
        db.add(log)
        db.commit()
        db.refresh(log)
        log_id = log.id
    finally:
        db.close()

    rows = 0
    status = "success"
    error = None
    try:
        rows = _run_with_deadline(runner, task_name)
    except TimeoutError as e:
        status = "failed"
        error = f"task exceeded {_TASK_DEADLINE_SEC}s wall-clock"
        logger.warning("admin task %s timed out", task_name)
    except Exception as e:  # noqa: BLE001 - record any failure
        status = "failed"
        error = str(e)
        logger.exception("admin task %s failed", task_name)

    db = SessionLocal()
    try:
        db.execute(
            SaAdminTaskLog.__table__.update()
            .where(SaAdminTaskLog.id == log_id)
            .values(
                finished_at=datetime.now(),
                status=status,
                rows_affected=rows,
                error=error,
            )
        )
        db.commit()
        row = db.execute(
            select(SaAdminTaskLog).where(SaAdminTaskLog.id == log_id)
        ).scalar_one()
        return _log_to_dict(row)
    finally:
        db.close()


def task_logs(task_name: str, limit: int = 20) -> list[dict]:
    db = SessionLocal()
    try:
        rows = db.execute(
            select(SaAdminTaskLog)
            .where(SaAdminTaskLog.task_name == task_name)
            .order_by(desc(SaAdminTaskLog.started_at))
            .limit(limit)
        ).scalars().all()
        return [_log_to_dict(r) for r in rows]
    finally:
        db.close()


def _log_to_dict(r: SaAdminTaskLog) -> dict:
    return {
        "id": r.id,
        "task_name": r.task_name,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "status": r.status,
        "rows_affected": r.rows_affected,
        "error": r.error,
        "triggered_by": r.triggered_by,
    }


# ---- data sources ----

DATASOURCES = [
    {"name": "akshare", "type": "eastmoney", "note": "A股行情/资金/板块/龙虎榜"},
    {"name": "tushare", "type": "tushare", "note": "财务/龙虎榜(备用)"},
]


def list_datasources() -> list[dict]:
    """Static data-source catalog (connectivity check is best-effort)."""
    return DATASOURCES


def test_datasource(name: str) -> dict:
    """Best-effort connectivity check for a data source."""
    if name == "akshare":
        try:
            import akshare as ak

            ver = getattr(ak, "__version__", "unknown")
            return {"name": name, "ok": True, "detail": f"akshare {ver} importable"}
        except Exception as e:  # noqa: BLE001
            return {"name": name, "ok": False, "detail": str(e)}
    if name == "tushare":
        try:
            import tushare  # noqa: F401

            return {"name": name, "ok": True, "detail": "tushare importable"}
        except Exception as e:  # noqa: BLE001
            return {"name": name, "ok": False, "detail": str(e)}
    return {"name": name, "ok": False, "detail": "unknown data source"}


# ---- user management ----


def list_users(db: Session) -> list[dict]:
    rows = db.execute(select(SaUser).order_by(SaUser.id)).scalars().all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "status": u.status,
            "created_at": u.created_at,
        }
        for u in rows
    ]


def update_user(db: Session, user_id: int, role: str | None = None, status: int | None = None) -> dict | None:
    u = db.get(SaUser, user_id)
    if u is None:
        return None
    if role is not None:
        u.role = role
    if status is not None:
        u.status = status
    db.commit()
    return {
        "id": u.id,
        "username": u.username,
        "role": u.role,
        "status": u.status,
    }
