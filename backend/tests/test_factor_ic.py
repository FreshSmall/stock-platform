"""Tests for factor IC analysis (pure functions, no DB)."""

import numpy as np
import pandas as pd

from app.factor import ic


def test_single_ic_perfect_positive():
    """Factor values perfectly aligned with returns → IC ≈ 1."""
    fv = pd.Series([1, 2, 3, 4, 5], index=list("abcde"))
    fr = pd.Series([0.1, 0.2, 0.3, 0.4, 0.5], index=list("abcde"))
    assert ic.single_ic(fv, fr) == pytest_approx(1.0)


def test_single_ic_perfect_negative():
    fv = pd.Series([1, 2, 3, 4, 5], index=list("abcde"))
    fr = pd.Series([0.5, 0.4, 0.3, 0.2, 0.1], index=list("abcde"))
    assert ic.single_ic(fv, fr) == pytest_approx(-1.0)


def test_single_ic_too_few_returns_none():
    fv = pd.Series([1, 2], index=list("ab"))
    fr = pd.Series([0.1, 0.2], index=list("ab"))
    assert ic.single_ic(fv, fr) is None


def test_single_ic_missing_values():
    """NaN factor values are dropped before correlation."""
    fv = pd.Series([1, 2, np.nan, 4, 5, 6], index=list("abcdef"))
    fr = pd.Series([0.1, 0.2, 0.3, 0.4, 0.5, 0.6], index=list("abcdef"))
    v = ic.single_ic(fv, fr)
    assert v is not None and 0.8 < v <= 1.0  # near-perfect on 5 valid pairs


def test_information_ratio():
    s = pd.Series([0.1, 0.2, 0.3, 0.4])
    ir = ic.information_ratio(s)
    assert ir is not None and ir > 0


def test_information_ratio_zero_std():
    s = pd.Series([0.5, 0.5, 0.5])
    assert ic.information_ratio(s) is None


def test_layered_returns_basic():
    fv = pd.Series(range(20), index=[f"s{i}" for i in range(20)], dtype=float)
    fr = pd.Series(range(20), index=[f"s{i}" for i in range(20)], dtype=float)
    layers = ic.layered_returns(fv, fr, n_layers=5)
    assert layers is not None
    assert len(layers) == 5
    # highest factor layer (5) should have highest mean return
    assert layers[-1]["mean_return"] > layers[0]["mean_return"]


def test_layered_returns_too_few():
    fv = pd.Series([1, 2], index=list("ab"))
    fr = pd.Series([0.1, 0.2], index=list("ab"))
    assert ic.layered_returns(fv, fr, n_layers=5) is None


def pytest_approx(expected, rel=1e-6):
    """Local approx helper to avoid importing pytest at module top."""
    class _A:
        def __eq__(self, other):
            return other is not None and abs(other - expected) < rel * max(abs(expected), 1)
    return _A()
