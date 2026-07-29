"""Tests for V1.5 Task A2: the ``require_admin_user`` dependency.

These are DB-backed (real ``stock_analysis`` via ``db_session``) and exercise
the dependency end-to-end through a tiny throwaway FastAPI app that mounts one
admin-guarded route, so we assert on the real HTTP status codes the dependency
raises rather than catching exceptions by hand.
"""

import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.deps import get_db, require_admin_user
from app.core.security import create_access_token
from app.models.user import SaUser
from app.services import user_service
from sqlalchemy.orm import Session

# A minimal app with a single admin-protected route. Built once per test so the
# dependency override (which injects the SAME db_session as the test fixture)
# can be wired in cleanly.


def _make_admin_app(db_session: Session) -> FastAPI:
    app = FastAPI()

    # Override get_db to return the test's session so the dependency and the
    # test share one transaction/identity-map (same pattern the main app uses
    # via the real SessionLocal, just pinned to the test session here).
    app.dependency_overrides[get_db] = lambda: db_session

    @app.get("/admin-only")
    def admin_only(_=Depends(require_admin_user)):
        return {"ok": True}

    return app


def _make_user(db_session: Session, *, role: str = "user", status: int = 1) -> tuple[int, str]:
    """Insert a user with the given role/status and return (id, jwt_token)."""
    username = f"adm_{uuid.uuid4().hex[:8]}"
    user = user_service.register(db_session, username, "pw-test-123")
    # register() defaults role='user', status=1; override directly for the
    # admin / disabled cases.
    user.role = role
    user.status = status
    db_session.commit()
    return user.id, create_access_token(user.id)


def test_require_admin_allows_admin_role(db_session):
    uid, token = _make_user(db_session, role="admin")
    app = _make_admin_app(db_session)
    try:
        r = TestClient(app).get("/admin-only", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json() == {"ok": True}
    finally:
        db_session.query(SaUser).filter_by(id=uid).delete()
        db_session.commit()


def test_require_admin_rejects_normal_user(db_session):
    uid, token = _make_user(db_session, role="user")
    app = _make_admin_app(db_session)
    try:
        r = TestClient(app).get("/admin-only", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403
        assert "Admin" in r.json()["detail"]
    finally:
        db_session.query(SaUser).filter_by(id=uid).delete()
        db_session.commit()


def test_require_admin_rejects_disabled_admin(db_session):
    uid, token = _make_user(db_session, role="admin", status=0)
    app = _make_admin_app(db_session)
    try:
        r = TestClient(app).get("/admin-only", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403
        assert "disabled" in r.json()["detail"]
    finally:
        db_session.query(SaUser).filter_by(id=uid).delete()
        db_session.commit()


def test_require_admin_rejects_missing_token(db_session):
    app = _make_admin_app(db_session)
    r = TestClient(app).get("/admin-only")
    assert r.status_code == 401


def test_require_admin_rejects_deleted_user(db_session):
    """A valid token whose user was deleted after issuance must 401."""
    uid, token = _make_user(db_session, role="admin")
    # delete the user, then call with the now-orphan token
    db_session.query(SaUser).filter_by(id=uid).delete()
    db_session.commit()
    app = _make_admin_app(db_session)
    r = TestClient(app).get("/admin-only", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    assert "not found" in r.json()["detail"]
