"""Tests for F1: password hashing, JWT issue/verify, user service, auth router.

The DB-backed tests hit the real ``stock_analysis`` DB via the ``db_session``
fixture and clean up every row they insert.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.errors import BizError
from app.core.security import (
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.main import app
from app.models.user import SaUser
from app.services import user_service

client = TestClient(app)


# --- pure crypto (no DB) ---------------------------------------------------


def test_password_hash_roundtrip():
    h = hash_password("secret123")
    assert h != "secret123"
    assert verify_password("secret123", h) is True
    assert verify_password("wrong", h) is False


def test_jwt_issue_decode():
    token = create_access_token(42)
    payload = decode_token(token)
    assert payload is not None
    assert payload["sub"] == "42"


def test_jwt_invalid_returns_none():
    assert decode_token("not-a-token") is None


# --- user_service (DB) -----------------------------------------------------


def test_register_and_login(db_session):
    user = user_service.register(db_session, "alice", "passw0rd!")
    assert user.id is not None
    user2, token = user_service.login(db_session, "alice", "passw0rd!")
    assert user2.id == user.id
    assert token  # non-empty
    # cleanup
    db_session.query(SaUser).filter_by(username="alice").delete()
    db_session.commit()


def test_register_duplicate_raises(db_session):
    user_service.register(db_session, "bob", "x")
    with pytest.raises(BizError):
        user_service.register(db_session, "bob", "y")
    db_session.query(SaUser).filter_by(username="bob").delete()
    db_session.commit()


def test_login_wrong_password_raises(db_session):
    user_service.register(db_session, "carol", "right")
    with pytest.raises(BizError):
        user_service.login(db_session, "carol", "wrong")
    db_session.query(SaUser).filter_by(username="carol").delete()
    db_session.commit()


# --- /auth router end-to-end -----------------------------------------------


def test_api_register_login_me_flow(db_session):
    r = client.post(
        "/api/v1/auth/register", json={"username": "dave_api", "password": "pw12345"}
    )
    assert r.status_code == 200
    assert r.json()["code"] == 0

    r = client.post(
        "/api/v1/auth/login", json={"username": "dave_api", "password": "pw12345"}
    )
    token = r.json()["data"]["token"]

    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["data"]["username"] == "dave_api"

    # me without token -> 401
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401

    # cleanup
    db_session.query(SaUser).filter_by(username="dave_api").delete()
    db_session.commit()


def test_api_login_wrong_password_is_biz_error(db_session):
    """Wrong password renders as a 200 + code != 0 (BizError convention)."""
    user_service.register(db_session, "erin_api", "good")
    try:
        r = client.post(
            "/api/v1/auth/login", json={"username": "erin_api", "password": "bad"}
        )
        assert r.status_code == 200
        assert r.json()["code"] == 1002
    finally:
        db_session.query(SaUser).filter_by(username="erin_api").delete()
        db_session.commit()


def test_api_me_rejects_garbage_token():
    """A malformed bearer token must yield 401, not 500."""
    r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401
