"""Admin router (BP-V1.5-010) — data sources, tasks, users.

All endpoints under ``/api/v1/admin`` and guarded by ``require_admin_user``.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id, get_db, require_admin_user
from app.models.user import SaUser
from app.services import admin_service

router = APIRouter(prefix="/admin", tags=["admin"])


def _ok(data=None, msg: str = "ok") -> dict:
    from app.main import api_ok

    return api_ok(data, msg)


# ---- data sources ----


@router.get("/datasources")
def datasources(_: SaUser = Depends(require_admin_user)) -> dict:
    return _ok(admin_service.list_datasources())


@router.post("/datasources/{name}/test")
def test_datasource(name: str, _: SaUser = Depends(require_admin_user)) -> dict:
    return _ok(admin_service.test_datasource(name))


# ---- tasks ----


@router.get("/tasks")
def tasks(_: SaUser = Depends(require_admin_user)) -> dict:
    return _ok(admin_service.list_tasks())


@router.post("/tasks/{name}/run")
def run_task(
    name: str, user: SaUser = Depends(require_admin_user)
) -> dict:
    try:
        if name in admin_service._LONG_TASKS:
            # V2.1 long task: submit in the background, return the run id —
            # the frontend polls GET /admin/tasks/runs/{run_id}.
            run_id = admin_service.run_task_async(
                name, triggered_by=f"manual:{user.username}"
            )
            return _ok({"run_id": run_id, "async": True}, msg="submitted")
        return _ok(admin_service.run_task(name, triggered_by=f"manual:{user.username}"))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/tasks/runs/{run_id}")
def get_run(run_id: int, _: SaUser = Depends(require_admin_user)) -> dict:
    data = admin_service.get_run(run_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return _ok(data)


@router.get("/tasks/runs/{run_id}/failures")
def run_failures(run_id: int, _: SaUser = Depends(require_admin_user)) -> dict:
    """Failure list of a long run, as downloadable rows (from result/error)."""
    data = admin_service.get_run(run_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    result = data.get("result") or {}
    failures = (
        result.get("failed")
        or result.get("frozen_failed")
        or result.get("misaligned_failed")
        or []
    )
    return _ok(
        {
            "run_id": run_id,
            "status": data.get("status"),
            "failures": [
                {"code": c} if isinstance(c, str) else c for c in failures
            ],
        }
    )


@router.get("/tasks/{name}/logs")
def task_logs(
    name: str,
    limit: int = Query(20, ge=1, le=100),
    _: SaUser = Depends(require_admin_user),
) -> dict:
    return _ok(admin_service.task_logs(name, limit))


# ---- users ----


@router.get("/users")
def users(db: Session = Depends(get_db), _: SaUser = Depends(require_admin_user)) -> dict:
    return _ok(admin_service.list_users(db))


# ---- data quality (V2.1 BP-V2.1-007) ----


@router.get("/quality/daily")
def quality_daily(
    date: str | None = Query(None),
    db: Session = Depends(get_db),
    _: SaUser = Depends(require_admin_user),
) -> dict:
    from datetime import date as _date

    from app.services import quality_service

    d = _date.fromisoformat(date) if date else None
    return _ok(quality_service.daily_report(db, d))


@router.get("/quality/trend")
def quality_trend(
    days: int = Query(30, ge=1, le=180),
    db: Session = Depends(get_db),
    _: SaUser = Depends(require_admin_user),
) -> dict:
    from app.services import quality_service

    return _ok(quality_service.trend(db, days))


@router.get("/quality/detail")
def quality_detail(
    date: str = Query(...),
    check: str | None = Query(None),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
    _: SaUser = Depends(require_admin_user),
) -> dict:
    """Stock-level anomaly detail for one check date (from the detail JSON)."""
    from datetime import date as _date

    from app.services import quality_service

    report = quality_service.daily_report(db, _date.fromisoformat(date))
    items = [
        r for r in report
        if (check is None or r["check_name"] == check)
        and (status is None or r["status"] == status)
    ]
    detail_rows = []
    for r in items:
        d = r.get("detail") or {}
        if isinstance(d, str):
            import json as _json

            try:
                d = _json.loads(d)
            except ValueError:
                d = {}
        for offender in d.get("offenders", []):
            detail_rows.append({"check_name": r["check_name"], **offender})
    return _ok({"date": date, "rows": detail_rows[:500]})


@router.post("/quality/check/run")
def quality_check_run(
    user: SaUser = Depends(require_admin_user),
) -> dict:
    try:
        return _ok(admin_service.run_task("quality_check", triggered_by=f"manual:{user.username}"))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/factor-health")
def factor_health(
    db: Session = Depends(get_db),
    _uid: int = Depends(get_current_user_id),
) -> dict:
    """Latest factor-health patrol results (V2.2 T2.7)."""
    from app.services import factor_health_service

    return _ok(factor_health_service.health_report(db))


@router.post("/factor-health/run")
def run_factor_health(user: SaUser = Depends(require_admin_user)) -> dict:
    """Trigger the weekly factor-health patrol on demand.

    The patrol (5 factors × multi-horizon IC series) runs about a minute —
    beyond the frontend's 30s axios budget — so it submits as a V2.1-style
    long task; poll ``GET /admin/tasks/runs/{run_id}`` until it leaves
    ``running``, then refetch ``GET /admin/factor-health``.
    """
    run_id = admin_service.run_task_async(
        "factor_health_check", triggered_by=f"manual:{user.username}"
    )
    return _ok({"run_id": run_id, "async": True}, msg="submitted")


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    role: str | None = None,
    status: int | None = None,
    db: Session = Depends(get_db),
    _: SaUser = Depends(require_admin_user),
) -> dict:
    data = admin_service.update_user(db, user_id, role, status)
    if data is None:
        return _ok(None, msg="user not found")
    return _ok(data)
