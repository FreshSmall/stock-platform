"""Integration tests for :mod:`app.services.market_service` (Task B1).

These tests run against the REAL ``stock_analysis`` database using the
``db_session`` fixture from ``tests/conftest.py``. They use 贵州茅台 (600519) as
the canonical test stock — it is guaranteed to exist by the seed data.
"""

from datetime import date

from app.models.stock import DailyPrice, StockPool
from app.services import market_service


def test_search_stocks_by_name(db_session) -> None:
    """search_stocks by Chinese name returns 600519."""
    rows = market_service.search_stocks(db_session, "茅台")
    assert rows, "expected at least one match for '茅台'"
    codes = {r.stock_code for r in rows}
    assert "600519" in codes


def test_search_stocks_by_code(db_session) -> None:
    """search_stocks by code returns the exact code."""
    rows = market_service.search_stocks(db_session, "600519")
    assert rows
    assert all(isinstance(r, StockPool) for r in rows)
    codes = {r.stock_code for r in rows}
    assert "600519" in codes


def test_search_stocks_empty_q(db_session) -> None:
    """An empty query returns an empty list (defensive guard)."""
    assert market_service.search_stocks(db_session, "") == []


def test_search_stocks_respects_limit(db_session) -> None:
    """A non-trivial limit caps the number of distinct codes returned."""
    # A single Chinese character matches many stocks, so this exercises the
    # dedupe-then-cap branch.
    rows = market_service.search_stocks(db_session, "股份", limit=3)
    assert len(rows) <= 3
    # Codes are unique after dedupe.
    codes = [r.stock_code for r in rows]
    assert len(codes) == len(set(codes))


def test_get_stock_info_known(db_session) -> None:
    """get_stock_info returns a row whose name contains 茅台."""
    row = market_service.get_stock_info(db_session, "600519")
    assert row is not None
    assert row.stock_name is not None
    assert "茅台" in row.stock_name


def test_get_stock_info_unknown(db_session) -> None:
    """get_stock_info returns None for a code that is not in the pool."""
    assert market_service.get_stock_info(db_session, "999999") is None


def test_get_kline_returns_ascending(db_session) -> None:
    """get_kline returns a non-empty list ordered ascending by trade_date."""
    rows = market_service.get_kline(db_session, "600519")
    assert rows, "expected K-line data for 600519"
    assert all(isinstance(r, DailyPrice) for r in rows)
    dates = [r.trade_date for r in rows]
    for prev, nxt in zip(dates, dates[1:]):
        assert prev <= nxt, f"K-line not ascending: {prev} -> {nxt}"


def test_get_kline_date_filter(db_session) -> None:
    """get_kline with start/end bounds only returns dates inside the window."""
    start = date(2026, 1, 1)
    end = date(2026, 1, 31)
    rows = market_service.get_kline(db_session, "600519", start=start, end=end)
    assert rows, "expected at least one January 2026 bar for 600519"
    for r in rows:
        assert start <= r.trade_date <= end
