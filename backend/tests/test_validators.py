"""Unit tests for app.data.validators (no DB, no network)."""

import logging

from app.data.validators import find_duplicate_keys, validate_daily_row


def test_validate_daily_row_valid() -> None:
    """A well-formed row validates as True."""
    row = {
        "stock_code": "600519",
        "trade_date": "2026-07-28",
        "open": 1270.0,
        "close": 1327.5,
        "high": 1330.0,
        "low": 1265.0,
        "volume": 12345,
        "amount": 1.6e9,
        "pct_change": 5.95,
        "turnover": 0.85,
    }
    assert validate_daily_row(row) is True


def test_validate_daily_row_missing_close() -> None:
    """A row without close is invalid."""
    row = {
        "stock_code": "600519",
        "trade_date": "2026-07-28",
        "close": None,
        "pct_change": 1.2,
    }
    assert validate_daily_row(row) is False


def test_validate_daily_row_missing_code() -> None:
    """A row without stock_code is invalid."""
    row = {"stock_code": None, "trade_date": "2026-07-28", "close": 10.0}
    assert validate_daily_row(row) is False


def test_validate_daily_row_abnormal_pct_logs_warning(caplog) -> None:
    """An abnormal pct_change is kept (True) but logs a warning."""
    row = {
        "stock_code": "000002",
        "trade_date": "2026-07-28",
        "close": 10.0,
        "pct_change": 25.0,  # > 20% threshold
    }
    with caplog.at_level(logging.WARNING, logger="app.data.validators"):
        result = validate_daily_row(row)
    assert result is True  # abnormal but still valid
    assert any("abnormal pct_change" in rec.message for rec in caplog.records)


def test_validate_daily_row_unparseable_pct_logs_warning(caplog) -> None:
    """An unparseable pct_change logs a warning but the row stays valid."""
    row = {
        "stock_code": "000003",
        "trade_date": "2026-07-28",
        "close": 10.0,
        "pct_change": "not-a-number",
    }
    with caplog.at_level(logging.WARNING, logger="app.data.validators"):
        result = validate_daily_row(row)
    assert result is True
    assert any("unparseable pct_change" in rec.message for rec in caplog.records)


def test_find_duplicate_keys_no_dups() -> None:
    """Distinct keys yield no duplicates."""
    rows = [
        {"stock_code": "A", "trade_date": "2026-07-01"},
        {"stock_code": "A", "trade_date": "2026-07-02"},
        {"stock_code": "B", "trade_date": "2026-07-01"},
    ]
    assert find_duplicate_keys(rows) == []


def test_find_duplicate_keys_finds_dup() -> None:
    """A repeated (code, date) pair is reported exactly once."""
    rows = [
        {"stock_code": "A", "trade_date": "2026-07-01"},
        {"stock_code": "A", "trade_date": "2026-07-01"},  # 2nd sight → dup
        {"stock_code": "A", "trade_date": "2026-07-01"},  # 3rd sight → no new
        {"stock_code": "B", "trade_date": "2026-07-01"},
    ]
    dups = find_duplicate_keys(rows)
    assert dups == [("A", "2026-07-01")]
