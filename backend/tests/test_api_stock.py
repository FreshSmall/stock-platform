"""HTTP-layer tests for the stock router (Task B1).

Uses :class:`fastapi.testclient.TestClient` against the real ``app`` (real DB
via ``get_db``). The tests assert the unified envelope shape (``code == 0``)
and the presence of canonical seed data (600519 贵州茅台).
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    """A TestClient bound to the real FastAPI app (real DB, no mock)."""
    return TestClient(app)


def test_api_search(client: TestClient) -> None:
    """GET /stock/search?q=茅台 returns code:0 with 600519 in the data list."""
    resp = client.get("/api/v1/stock/search", params={"q": "茅台"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert isinstance(data, list)
    codes = {item["stock_code"] for item in data}
    assert "600519" in codes


def test_api_search_rejects_empty_q(client: TestClient) -> None:
    """An empty q is rejected by Query(min_length=1) with HTTP 422."""
    resp = client.get("/api/v1/stock/search", params={"q": ""})
    assert resp.status_code == 422


def test_api_get_info(client: TestClient) -> None:
    """GET /stock/600519 returns the latest snapshot for 贵州茅台."""
    resp = client.get("/api/v1/stock/600519")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert data is not None
    assert data["stock_code"] == "600519"
    assert data["stock_name"] is not None
    assert "茅台" in data["stock_name"]


def test_api_get_info_unknown(client: TestClient) -> None:
    """An unknown code returns code:0 with data=None and a not-found msg."""
    resp = client.get("/api/v1/stock/999999")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"] is None
    assert body["msg"]


def test_api_kline(client: TestClient) -> None:
    """GET /stock/600519/kline returns a non-empty K-line list for Jan 2026."""
    resp = client.get(
        "/api/v1/stock/600519/kline",
        params={"start": "2026-01-01", "end": "2026-01-31"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert isinstance(data, list)
    assert data, "expected at least one January 2026 bar for 600519"
    # First bar should have trade_date + OHLCV fields present in the schema.
    first = data[0]
    assert "trade_date" in first
    for field in ("open", "close", "high", "low", "volume"):
        assert field in first
