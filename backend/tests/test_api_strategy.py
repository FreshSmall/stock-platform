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


def test_strategy_list_v2_now_available():
    """V2 strategies (ema/trend/leader/board/lowbuy/breakout) are implemented
    and available (no longer greyed-out placeholders)."""
    resp = client.get("/api/v1/strategy")
    items = resp.json()["data"]
    by_name = {it["name"]: it for it in items}
    for v2_name in ("ema", "trend", "leader", "board", "lowbuy", "breakout"):
        assert v2_name in by_name, f"{v2_name} missing"
        assert by_name[v2_name]["available"] is True, f"{v2_name} not available"
    # all 8 strategies are now usable
    assert all(it["available"] for it in items)
    assert len(items) == 8
