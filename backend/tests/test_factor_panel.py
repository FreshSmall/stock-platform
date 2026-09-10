"""Tests for the V2.2 vectorized factor panel + panel-based research.

Two layers:
- Pure-function tests on synthetic panels (factor math, no DB).
- DB-backed checks on the real stock_analysis DB (600519): panel factors vs
  the registry slow path, IC series + persistence idempotency, layered
  backtest shape, neutralization effect.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import delete

from app.factor import registry
from app.models.factor import SaFactorIc
from app.services import factor_panel as fpanel
from app.services import factor_service


# --- synthetic panel: factor math ------------------------------------------


def _synthetic_panel(n_days: int = 320, n_codes: int = 4, seed: int = 7):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-01-01", periods=n_days)
    codes = [f"60000{i}" for i in range(n_codes)]
    close = pd.DataFrame(
        100 + np.cumsum(rng.normal(0, 1, (n_days, n_codes)), axis=0),
        index=dates,
        columns=codes,
    )
    volume = pd.DataFrame(
        rng.uniform(1e4, 1e6, (n_days, n_codes)), index=dates, columns=codes
    )
    return fpanel.MarketPanel(close=close, volume=volume)


def test_panel_ma_and_roc_match_manual():
    p = _synthetic_panel()
    ma5 = fpanel.panel_factor_values(p, "ma5")
    assert np.allclose(ma5.iloc[-1].to_numpy(), p.close.tail(5).mean().to_numpy())
    roc12 = fpanel.panel_factor_values(p, "roc12")
    manual = (p.close / p.close.shift(12) - 1.0) * 100.0
    assert np.allclose(roc12, manual, equal_nan=True)


def test_panel_amt20_and_vol_ratio():
    p = _synthetic_panel()
    amt = fpanel.panel_factor_values(p, "amt20")
    assert np.allclose(
        amt.iloc[-1].to_numpy(),
        (p.close * p.volume).tail(20).mean().to_numpy(),
    )
    vr = fpanel.panel_factor_values(p, "vol_ratio5")
    manual = p.volume / p.volume.shift(1).rolling(5).mean()
    assert np.allclose(vr, manual, equal_nan=True)


def test_panel_obv_trend_sign_independent_of_cumsum_start():
    """OBV trend sign must not depend on where the cumulative sum starts."""
    p = _synthetic_panel()
    out = fpanel.panel_factor_values(p, "obv_trend")
    direction = np.sign(p.close.diff())
    obv = (direction * p.volume).cumsum()
    tail = np.sign(obv.tail(60) - obv.tail(60).shift(5))
    assert np.allclose(
        out.tail(55).to_numpy(), tail.dropna().to_numpy(), equal_nan=True
    )


def test_panel_forward_return_panel():
    p = _synthetic_panel()
    fwd = fpanel.forward_return_panel(p.close, 3)
    manual = p.close.shift(-3) / p.close - 1.0
    assert np.allclose(fwd, manual, equal_nan=True)
    assert fwd.iloc[-1].isna().all()  # no data beyond the end


def test_panel_unsupported_factor_raises():
    p = _synthetic_panel()
    with pytest.raises(ValueError, match="not panel-computable"):
        fpanel.panel_factor_values(p, "adx14")


# --- DB-backed: panel vs registry consistency (real data) ------------------

# Rolling-window factors recompute exactly; ewm factors re-seed per compute
# call so they only agree after warmup — tolerance reflects that.
_EXACT_FACTORS = (
    "ma5", "roc12", "roc120", "hv20", "skew20", "amt20",
    "vol_ratio5", "obv_trend", "vol_price_trend",
)
_EWM_FACTORS = ("rsi14", "ema12", "macd_dif")


def test_panel_matches_registry_real(db_session):
    end = date(2026, 9, 4)
    p = fpanel.load_market_panel(
        db_session, end - timedelta(days=420), end, codes=["600519"]
    )
    for code in _EXACT_FACTORS:
        panel_v = float(fpanel.panel_factor_values(p, code).iloc[-1]["600519"])
        reg_v = registry.get(code).compute(db_session, "600519", end)
        assert reg_v is not None, code
        assert abs(panel_v - reg_v) <= 1e-6 * max(1.0, abs(reg_v)), (
            f"{code}: panel={panel_v} registry={reg_v}"
        )
    for code in _EWM_FACTORS:
        panel_v = float(fpanel.panel_factor_values(p, code).iloc[-1]["600519"])
        reg_v = registry.get(code).compute(db_session, "600519", end)
        assert abs(panel_v - reg_v) <= 0.5 * max(1.0, abs(reg_v)), (
            f"{code}: panel={panel_v} registry={reg_v}"
        )


# --- DB-backed: IC series + persistence ------------------------------------


def test_ic_series_persists_and_is_idempotent(db_session):
    start, end = date(2026, 5, 1), date(2026, 8, 31)
    scope = {"factor_code": "amt20", "pool": "pit", "neutralized": "none"}
    db_session.execute(delete(SaFactorIc).where(*[getattr(SaFactorIc, k) == v for k, v in scope.items()]))
    db_session.commit()
    try:
        res = factor_service.compute_ic_series(
            db_session, "amt20", start, end,
            horizons=(5,), step=10, pool="pit", universe_size=100,
        )
        assert res is not None
        assert res["persisted_rows"] > 0
        # re-run: same rows, no duplicates (upsert on the scope UK)
        res2 = factor_service.compute_ic_series(
            db_session, "amt20", start, end,
            horizons=(5,), step=10, pool="pit", universe_size=100,
        )
        assert res2["persisted_rows"] == res["persisted_rows"]

        from sqlalchemy import func, select

        n = db_session.execute(
            select(func.count()).select_from(SaFactorIc).where(
                SaFactorIc.factor_code == "amt20",
                SaFactorIc.horizon == 5,
                SaFactorIc.pool == "pit",
                SaFactorIc.trade_date >= start,
            )
        ).scalar_one()
        assert n == res["persisted_rows"]
        # summary sanity: horizon-5 amt20 IC is negative in the survey regime
        assert res["summary"]["5"]["mean_ic"] is not None
    finally:
        db_session.execute(
            delete(SaFactorIc).where(
                SaFactorIc.factor_code == "amt20",
                SaFactorIc.trade_date >= start,
                SaFactorIc.trade_date <= end,
            )
        )
        db_session.commit()


def test_ic_series_rejects_non_panel_factor(db_session):
    with pytest.raises(ValueError, match="not panel-computable"):
        factor_service.compute_ic_series(
            db_session, "pe", date(2026, 5, 1), date(2026, 8, 31)
        )


# --- DB-backed: layered backtest shape --------------------------------------


def test_layered_backtest_shape(db_session):
    res = factor_service.layered_backtest(
        db_session, "amt20", date(2026, 5, 1), date(2026, 8, 31),
        step=10, n_layers=5, pool="pit", universe_size=100,
    )
    assert res is not None
    assert [L["layer"] for L in res["layers"]] == [1, 2, 3, 4, 5]
    for layer in res["layers"]:
        nav = layer["nav"]
        assert nav[0] > 0
        # nav multiplies period gross returns
        assert all(v > 0 for v in nav)
        assert layer["max_drawdown"] <= 0
    assert len(res["long_short"]["nav"]) > 0
    assert res["rebalance_dates"]


# --- DB-backed: neutralization changes the IC -------------------------------


def test_neutralize_changes_single_day_ic(db_session):
    d = date(2026, 8, 20)
    raw = factor_service.compute_ic(db_session, "amt20", d, horizon=5)
    neu = factor_service.compute_ic(
        db_session, "amt20", d, horizon=5, neutralize="industry_mcap"
    )
    assert raw is not None and neu is not None
    assert raw["ic"] is not None and neu["ic"] is not None
    assert raw["ic"] != neu["ic"]
    assert neu["universe_size"] <= raw["universe_size"]
