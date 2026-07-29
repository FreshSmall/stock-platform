"""Tests for V1.5 admin API: auth guard + task/user management.

DB-backed (real stock_analysis). Creates a temporary admin + normal user,
exercises the /admin endpoints, and cleans up.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.user import SaUser
from app.services import user_service

client = TestClient(app)


def _make_user(db, role, status=1):
    uname = f"admapi_{uuid.uuid4().hex[:8]}"
    u = user_service.register(db, uname, "pw-test-123")
    u.role = role
    u.status = status
    db.commit()
    return u


@pytest.fixture
def admin_and_token(db_session):
    u = _make_user(db_session, "admin")
    from app.core.security import create_access_token

    token = create_access_token(u.id)
    try:
        yield u, token
    finally:
        db_session.query(SaUser).filter_by(id=u.id).delete()
        db_session.commit()


def test_admin_endpoints_reject_normal_user(db_session):
    u = _make_user(db_session, "user")
    from app.core.security import create_access_token

    token = create_access_token(u.id)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        for url in ["/api/v1/admin/tasks", "/api/v1/admin/users", "/api/v1/admin/datasources"]:
            assert client.get(url, headers=headers).status_code == 403
    finally:
        db_session.query(SaUser).filter_by(id=u.id).delete()
        db_session.commit()


def test_admin_endpoints_reject_no_token():
    for url in ["/api/v1/admin/tasks", "/api/v1/admin/users"]:
        assert client.get(url).status_code == 401


def test_list_tasks_and_datasources(admin_and_token):
    _, token = admin_and_token
    h = {"Authorization": f"Bearer {token}"}
    r = client.get("/api/v1/admin/tasks", headers=h)
    assert r.status_code == 200
    names = {t["task_name"] for t in r.json()["data"]}
    assert "sentiment_sync" in names and "daily_k_sync" in names

    r2 = client.get("/api/v1/admin/datasources", headers=h)
    assert r2.status_code == 200
    assert {d["name"] for d in r2.json()["data"]} >= {"akshare"}


def test_run_unknown_task_404(admin_and_token):
    _, token = admin_and_token
    r = client.post("/api/v1/admin/tasks/no_such_task/run", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404


def test_run_sentiment_task_logs(admin_and_token, db_session):
    """Running the sentiment task creates a log row (success or failed, not crash)."""
    _, token = admin_and_token
    r = client.post(
        "/api/v1/admin/tasks/sentiment_sync/run", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] in ("success", "failed")
    assert data["triggered_by"].startswith("manual:")
    # logs endpoint sees it
    logs = client.get(
        "/api/v1/admin/tasks/sentiment_sync/logs", headers={"Authorization": f"Bearer {token}"}
    ).json()["data"]
    assert any(l["id"] == data["id"] for l in logs)


def test_user_list_and_update(admin_and_token, db_session):
    _, token = admin_and_token
    h = {"Authorization": f"Bearer {token}"}
    target = _make_user(db_session, "user")
    try:
        r = client.get("/api/v1/admin/users", headers=h)
        assert r.status_code == 200
        assert any(u["id"] == target.id for u in r.json()["data"])

        # disable
        r2 = client.patch(f"/api/v1/admin/users/{target.id}?status=0", headers=h)
        assert r2.status_code == 200
        assert r2.json()["data"]["status"] == 0

        # promote
        r3 = client.patch(f"/api/v1/admin/users/{target.id}?role=admin", headers=h)
        assert r3.json()["data"]["role"] == "admin"

        # not found
        r4 = client.patch("/api/v1/admin/users/9999999?status=1", headers=h)
        assert r4.json()["msg"] == "user not found"
    finally:
        db_session.query(SaUser).filter_by(id=target.id).delete()
        db_session.commit()
