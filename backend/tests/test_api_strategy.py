"""Tests for the /strategy list endpoint."""
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_strategy_list_returns_ma_and_macd_available():
    resp = client.get("/api/v1/strategy")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    items = body["data"]
    names = {it["name"]: it for it in items}
    assert names["ma"]["available"] is True
    assert names["macd"]["available"] is True
    # ma exposes fast/slow params
    pnames = {p["name"] for p in names["ma"]["params"]}
    assert pnames == {"fast", "slow"}


def test_strategy_list_has_v2_greyed_out():
    resp = client.get("/api/v1/strategy")
    items = resp.json()["data"]
    v2 = {it["name"] for it in items if not it["available"]}
    # V2 placeholders are not usable in V1
    assert {"ema", "trend", "leader", "board", "lowbuy", "breakout"} <= v2
