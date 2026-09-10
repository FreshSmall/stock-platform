"""Tests for the V2 factor framework (BP-V2-001).

Two layers:
- Pure registry tests (no DB): registration, categories, lookup.
- DB-backed compute tests on the real stock_analysis DB (600519).
"""

from datetime import date
import math


from app.factor import registry


# --- registry (pure) ------------------------------------------------------


def test_registry_has_6_categories():
    cats = set(registry.categories())
    assert cats == {"trend", "momentum", "volatility", "volume", "fundamental", "sentiment"}


def test_registry_has_30_plus_factors():
    assert len(registry.all_factors()) >= 30


def test_by_category_filters_correctly():
    trend = registry.by_category("trend")
    assert all(f.category == "trend" for f in trend)
    assert len(trend) >= 5  # ma5/10/20, ema12/26, macd_dif, adx, supertrend


def test_get_returns_factor_or_none():
    assert registry.get("ma5") is not None
    assert registry.get("nonexistent") is None


def test_factor_has_required_attributes():
    for f in registry.all_factors():
        assert f.code, f"factor missing code: {f}"
        assert f.name, f"factor missing name: {f}"
        assert f.category in ("trend", "momentum", "volatility", "volume", "fundamental", "sentiment")


def test_ma_periods_registered():
    for p in (5, 10, 20):
        assert registry.get(f"ma{p}") is not None


def test_rsi_periods_registered():
    for p in (6, 12, 24):
        assert registry.get(f"rsi{p}") is not None


# --- DB-backed compute (real data) ---------------------------------------


def test_ma5_compute_real(db_session):
    """600519 MA5 is a positive number on a known trading day."""
    f = registry.get("ma5")
    v = f.compute(db_session, "600519", date(2026, 7, 28))
    assert v is not None and v > 0


def test_pe_compute_real(db_session):
    """600519 PE from stock_pool is a positive number."""
    f = registry.get("pe")
    v = f.compute(db_session, "600519", date(2026, 7, 28))
    assert v is not None and v > 0


def test_compute_unknown_stock_returns_none(db_session):
    """A sentinel code with no data returns None, not an error."""
    f = registry.get("ma5")
    assert f.compute(db_session, "ZZNOSTOCK", date(2026, 7, 28)) is None


def test_adx_compute_in_range(db_session):
    """ADX is in [0, 100]."""
    f = registry.get("adx14")
    v = f.compute(db_session, "600519", date(2026, 7, 28))
    if v is not None:  # may be None if insufficient data
        assert 0 <= v <= 100


def test_supertrend_is_sign(db_session):
    """SuperTrend returns +1 / -1 / 0 / None."""
    f = registry.get("supertrend")
    v = f.compute(db_session, "600519", date(2026, 7, 28))
    assert v in (None, 1.0, -1.0, 0.0)


def test_supertrend_flips_on_crash(db_session):
    """Regression (ratchet fix): 300328's June-2026 crash (close 19.28 → 14.06
    over 6/17–6/29) must flip SuperTrend to -1. The old no-ratchet version
    let the stop line retreat with price and stayed +1 through the crash."""
    f = registry.get("supertrend")
    assert f.compute(db_session, "300328", date(2026, 6, 23)) == 1.0  # before the break
    assert f.compute(db_session, "300328", date(2026, 6, 25)) == -1.0  # close 17.02 breaks the stop
    assert f.compute(db_session, "300328", date(2026, 6, 29)) == -1.0


def test_vol_ratio_positive(db_session):
    f = registry.get("vol_ratio5")
    v = f.compute(db_session, "600519", date(2026, 7, 28))
    if v is not None:
        assert v >= 0


def test_cci14_compute_real(db_session):
    """Regression: cci14 raised AttributeError (ndarray has no .abs) since
    inception and never produced a value; it must compute to a finite number."""
    f = registry.get("cci14")
    v = f.compute(db_session, "600519", date(2026, 7, 28))
    assert v is not None
    assert math.isfinite(v)
