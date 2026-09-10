"""Tests for the V2.1 cross-sectional neutralization tool (D6)."""

import numpy as np
import pandas as pd
import pytest

from app.services import neutralize


def _series(values, index=None):
    return pd.Series(values, index=index or [f"s{i}" for i in range(len(values))])


class TestNeutralize:
    def test_requires_regressor(self):
        with pytest.raises(ValueError):
            neutralize.neutralize_cross_section(_series([1.0, 2.0, 3.0]))

    def test_injected_industry_effect_removed(self):
        """Two industries with a constant level offset → residuals identical."""
        # industry A values ~ N(10), industry B ~ N(20); within-industry noise only
        rng = np.random.default_rng(7)
        ind = pd.Series(["A"] * 50 + ["B"] * 50)
        vals = pd.Series(
            np.concatenate([10 + rng.normal(0, 1, 50), 20 + rng.normal(0, 1, 50)])
        )
        resid = neutralize.neutralize_cross_section(vals, industries=ind)
        assert resid.notna().all()
        # The industry mean difference must be gone: residual means equal.
        a = resid[ind == "A"].mean()
        b = resid[ind == "B"].mean()
        assert abs(a - b) < 1e-8
        # and residual overall mean ≈ 0 (intercept absorbed)
        assert abs(resid.mean()) < 1e-8

    def test_injected_mktcap_effect_removed(self):
        """Values proportional to log-mktcap → residuals ≈ 0."""
        idx = [f"s{i}" for i in range(80)]
        mcap = pd.Series(np.linspace(1e8, 5e10, 80), index=idx)
        vals = pd.Series(3.0 * np.log(mcap.to_numpy()) + 1.0, index=idx)
        resid = neutralize.neutralize_cross_section(vals, log_mktcap=mcap)
        assert resid.notna().all()
        assert resid.abs().max() < 1e-6

    def test_combined_regressors(self):
        rng = np.random.default_rng(11)
        idx = [f"s{i}" for i in range(80)]
        ind = pd.Series(["X"] * 40 + ["Y"] * 40, index=idx)
        mcap = pd.Series(np.exp(rng.normal(23, 1, 80)), index=idx)
        vals = pd.Series(
            (ind == "Y").astype(float).to_numpy() * 5.0
            + 2.0 * np.log(mcap.to_numpy())
            + rng.normal(0, 0.5, 80),
            index=idx,
        )
        resid = neutralize.neutralize_cross_section(vals, industries=ind, log_mktcap=mcap)
        assert abs(resid[ind == "X"].mean() - resid[ind == "Y"].mean()) < 1e-8
        # correlation of residual with log mktcap ≈ 0
        corr = np.corrcoef(resid.to_numpy(), np.log(mcap.to_numpy()))[0, 1]
        assert abs(corr) < 0.15

    def test_missing_regressors_yield_nan_not_dropped_silently(self):
        ind = pd.Series(["A", "A", None, "B"], index=[f"s{i}" for i in range(4)])
        vals = _series([1.0, 2.0, 3.0, 4.0])
        resid = neutralize.neutralize_cross_section(vals, industries=ind)
        assert resid.isna().sum() == 1  # the row with unknown industry

    def test_single_industry_degenerate(self):
        """One industry only → dummies drop out; still runs via the intercept."""
        ind = pd.Series(["A"] * 10)
        vals = _series(np.arange(10, dtype=float).tolist())
        resid = neutralize.neutralize_cross_section(vals, industries=ind)
        # intercept-only fit → residuals = values - mean
        assert np.allclose(resid.to_numpy(), vals.to_numpy() - vals.mean())
