"""Tests for V1.5 chip_service (reads the live chip_distribution table)."""

from app.services import chip_service


def test_get_chip_latest_real_stock(db_session):
    """600519 has chip data; latest snapshot returns parsed fields."""
    data = chip_service.get_chip(db_session, "600519")
    assert data is not None
    assert data["trade_date"] is not None
    # profit_ratio is 0..1 in the source table
    assert data["profit_ratio"] is None or 0.0 <= data["profit_ratio"] <= 1.0
    assert data["avg_cost"] is not None and data["avg_cost"] > 0


def test_get_chip_unknown_returns_none(db_session):
    """A sentinel code with no chip row returns None."""
    assert chip_service.get_chip(db_session, "ZZNOCODE") is None


def test_get_chip_distribution_parsed(db_session):
    """The distribution column parses to a list (or None) rather than a str."""
    data = chip_service.get_chip(db_session, "600519")
    assert data is not None
    d = data["distribution"]
    assert d is None or isinstance(d, list)
