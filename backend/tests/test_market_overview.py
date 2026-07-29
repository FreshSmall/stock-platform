"""Integration tests for the market-overview layer (Task B3).

Two layers are covered:

* Service-layer (:mod:`app.services.market_service`) —
  :func:`get_indices`, :func:`get_market_summary`, :func:`get_hot_stocks` run
  against the REAL ``stock_analysis`` DB via the ``db_session`` fixture.
* HTTP-layer (:mod:`app.api.market`) — the three ``/api/v1/market/*`` endpoints
  via :class:`fastapi.testclient.TestClient`.

The indices endpoint returns placeholder ``None`` quotes today (indices are
not in ``daily_prices``); the breadth/hot-stock tests assert positive counts
and correct ordering against the live data.
"""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import market_service


@pytest.fixture(scope="module")
def client() -> TestClient:
    """A TestClient bound to the real FastAPI app (real DB, no mock)."""
    return TestClient(app)


# --------------------------------------------------------------------------- #
# get_indices
# --------------------------------------------------------------------------- #


def test_get_indices_returns_three(db_session) -> None:
    """get_indices returns exactly 3 rows with the canonical index names."""
    rows = market_service.get_indices(db_session)
    assert len(rows) == 3
    names = [r["name"] for r in rows]
    assert names == ["上证指数", "深证成指", "创业板指"]
    codes = [r["code"] for r in rows]
    assert codes == ["000001", "399001", "399006"]


def test_get_indices_has_close_and_pct_change_keys(db_session) -> None:
    """Each index row carries close/pct_change keys (None until B4 lands)."""
    for r in market_service.get_indices(db_session):
        assert set(r.keys()) == {"code", "name", "close", "pct_change"}


# --------------------------------------------------------------------------- #
# get_market_summary
# --------------------------------------------------------------------------- #


def test_get_market_summary_positive_counts(db_session) -> None:
    """On a real trading day the breadth totals are positive and sum sensibly."""
    s = market_service.get_market_summary(db_session)
    assert isinstance(s["trade_date"], date)
    assert s["advance_count"] > 0
    assert s["decline_count"] > 0
    assert s["flat_count"] >= 0
    assert s["total_amount"] is not None
    assert s["total_amount"] > 0


def test_get_market_summary_field_types(db_session) -> None:
    """Counts are plain ints and total_amount is a Decimal (matches the schema)."""
    s = market_service.get_market_summary(db_session)
    assert isinstance(s["advance_count"], int)
    assert isinstance(s["decline_count"], int)
    assert isinstance(s["flat_count"], int)
    assert isinstance(s["total_amount"], Decimal)


# --------------------------------------------------------------------------- #
# get_hot_stocks
# --------------------------------------------------------------------------- #


def test_get_hot_stocks_by_amount(db_session) -> None:
    """Top-by-amount is ordered by amount desc and capped at the limit."""
    rows = market_service.get_hot_stocks(db_session, sort="amount", limit=10)
    assert len(rows) <= 10
    assert rows, "expected at least one hot stock on the latest trading day"
    amounts = [r["amount"] for r in rows]
    # Non-increasing (allow equal because ties are possible).
    for prev, nxt in zip(amounts, amounts[1:]):
        assert prev >= nxt, f"amount not descending: {prev} -> {nxt}"


def test_get_hot_stocks_by_pct_change(db_session) -> None:
    """Top-by-pct_change is ordered by pct_change desc."""
    rows = market_service.get_hot_stocks(db_session, sort="pct_change", limit=5)
    assert len(rows) == 5
    changes = [r["pct_change"] for r in rows]
    for prev, nxt in zip(changes, changes[1:]):
        assert prev >= nxt, f"pct_change not descending: {prev} -> {nxt}"


def test_get_hot_stocks_respects_limit(db_session) -> None:
    """A smaller limit never returns more rows than asked for."""
    rows = market_service.get_hot_stocks(db_session, sort="amount", limit=3)
    assert len(rows) <= 3


def test_get_hot_stocks_has_names(db_session) -> None:
    """At least some rows carry a non-null stock_name joined from stock_pool."""
    rows = market_service.get_hot_stocks(db_session, sort="amount", limit=20)
    assert rows
    named = [r for r in rows if r["stock_name"] is not None]
    assert named, "expected at least one hot stock with a name in stock_pool"


def test_get_hot_stocks_row_shape(db_session) -> None:
    """Each hot-stock row exposes the full HotStock field set."""
    rows = market_service.get_hot_stocks(db_session, sort="amount", limit=5)
    for r in rows:
        assert set(r.keys()) == {
            "stock_code",
            "stock_name",
            "close",
            "pct_change",
            "amount",
        }


# --------------------------------------------------------------------------- #
# HTTP layer
# --------------------------------------------------------------------------- #


def test_api_market_indices(client: TestClient) -> None:
    """GET /market/indices returns code:0 with a 3-element list."""
    resp = client.get("/api/v1/market/indices")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert isinstance(body["data"], list)
    assert len(body["data"]) == 3
    names = [item["name"] for item in body["data"]]
    assert names == ["上证指数", "深证成指", "创业板指"]


def test_api_market_summary(client: TestClient) -> None:
    """GET /market/summary returns code:0 with int advance_count."""
    resp = client.get("/api/v1/market/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert data is not None
    assert isinstance(data["advance_count"], int)
    assert isinstance(data["decline_count"], int)
    assert isinstance(data["flat_count"], int)
    assert data["trade_date"] is not None


def test_api_market_hot_stocks(client: TestClient) -> None:
    """GET /market/hot-stocks returns code:0 with a non-empty list."""
    resp = client.get("/api/v1/market/hot-stocks", params={"sort": "amount", "limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert isinstance(data, list)
    assert len(data) <= 5
    assert data, "expected at least one hot stock"


def test_api_market_hot_stocks_rejects_bad_sort(client: TestClient) -> None:
    """An invalid sort value is rejected by Query(pattern=...) with HTTP 422."""
    resp = client.get(
        "/api/v1/market/hot-stocks", params={"sort": "volume"}
    )
    assert resp.status_code == 422
