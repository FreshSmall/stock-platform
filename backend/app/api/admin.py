"""Admin router (BP-V1.5-010) — data sources, tasks, users.

All endpoints under ``/api/v1/admin`` and guarded by ``require_admin_user``.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_admin_user
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
        return _ok(admin_service.run_task(name, triggered_by=f"manual:{user.username}"))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


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
