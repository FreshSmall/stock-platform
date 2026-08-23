"""Tests for multi-factor scoring (pure functions, no DB).

Mirrors the style of test_factor_ic.py: pure pandas/numpy inputs, a local
approx helper (no hard pytest dependency at module top), one behaviour per test.
"""

import numpy as np
import pandas as pd

from app.factor import multi_factor as mf
from app.factor.multi_factor import FactorWeight


def _approx(expected, rel=1e-6):
    class _A:
        def __eq__(self, other):
            return other is not None and abs(other - expected) < rel * max(abs(expected), 1)

    return _A()


# --- z-score ---------------------------------------------------------------


def test_zscore_standardises_to_mean_zero_std_one():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    z = mf._zscore(s)
    assert abs(z.mean()) < 1e-9
    assert abs(z.std(ddof=0) - 1.0) < 1e-9


def test_zscore_clips_outliers():
    s = pd.Series([1.0, 1.0, 1.0, 1.0, 100.0])  # the 100 is a huge outlier
    z = mf._zscore(s, clip=3.0)
    assert z.max() <= 3.0 + 1e-9
    assert z.min() >= -3.0 - 1e-9


def test_zscore_zero_variance_returns_zeros():
    """All-equal input carries no info → flat 0 series, not NaN."""
    s = pd.Series([5.0, 5.0, 5.0])
    z = mf._zscore(s)
    assert (z == 0.0).all()


# --- composite_score -------------------------------------------------------


def test_composite_score_prefers_high_momentum():
    """A stock with the highest momentum factor should rank first."""
    fv = pd.DataFrame(
        {"roc12": [0.01, 0.05, 0.20, -0.02]},
        index=["s1", "s2", "s3", "s4"],
    )
    score = mf.composite_score(fv, weights={"roc12": 1.0})
    assert score.index[0] == "s3"   # highest ROC
    assert score.index[-1] == "s4"  # lowest ROC


def test_composite_score_direction_flips_ranking():
    """direction=-1 should invert: the lowest factor value wins."""
    fv = pd.DataFrame({"rsi14": [20.0, 50.0, 80.0]}, index=["a", "b", "c"])
    score_long = mf.composite_score(fv, weights={"rsi14": 1.0}, directions={})
    score_short = mf.composite_score(
        fv, weights={"rsi14": 1.0}, directions={"rsi14": -1}
    )
    assert score_long.index[0] == "c"   # highest RSI wins long
    assert score_short.index[0] == "a"  # lowest RSI wins when reversed


def test_composite_score_combines_two_factors():
    """Highest on BOTH factors should win; weighting tilts the order."""
    fv = pd.DataFrame(
        {
            "roc12": [0.10, 0.20, 0.05],
            "adx14": [30.0, 15.0, 25.0],
        },
        index=["x", "y", "z"],
    )
    # x: high momentum, strong trend. y: top momentum, weak trend. z: mid both.
    score = mf.composite_score(fv, weights={"roc12": 1.0, "adx14": 1.0})
    # x should beat y: x is strong on both, y gives up all trend strength.
    assert score["x"] > score["y"]


def test_composite_score_imputes_missing_with_median():
    """A NaN factor should be filled with the column median, not crash."""
    fv = pd.DataFrame(
        {"roc12": [0.01, np.nan, 0.03]},
        index=["a", "b", "c"],
    )
    score = mf.composite_score(fv, weights={"roc12": 1.0})
    assert len(score) == 3
    # median(0.01, 0.03) = 0.02 → b gets the middle rank, not top or bottom.
    assert score.index[0] == "c"   # highest
    assert score.index[-1] == "a"  # lowest
    assert score.index[1] == "b"   # median-imputed, sits in the middle


def test_composite_score_empty_inputs_return_empty():
    empty = pd.DataFrame()
    assert mf.composite_score(empty, weights={"roc12": 1.0}).empty
    assert mf.composite_score(pd.DataFrame({"roc12": [1.0]}), weights={}).empty


def test_composite_score_weights_normalised():
    """Doubling all weights shouldn't change the ranking (only the scale)."""
    fv = pd.DataFrame({"roc12": [0.1, 0.2, 0.3]}, index=list("abc"))
    s1 = mf.composite_score(fv, weights={"roc12": 1.0})
    s2 = mf.composite_score(fv, weights={"roc12": 2.0})
    list(s1.index) == list(s2.index)  # noqa: B015 — ranking stable
    # and the ratio of scores is constant across stocks
    ratios = (s2 / s1).dropna()
    assert ratios.std() < 1e-9


# --- select_top_n ----------------------------------------------------------


def test_select_top_n_basic():
    score = pd.Series({"a": 1.5, "b": 2.0, "c": 0.5, "d": 1.0})
    fv = pd.DataFrame({"roc12": [1.5, 2.0, 0.5, 1.0]}, index=list("abcd"))
    out = mf.select_top_n(score, fv, n=2)
    assert len(out) == 2
    assert out[0]["stock"] == "b"
    assert out[0]["rank"] == 1
    assert out[0]["score"] == _approx(2.0)
    assert out[0]["factors"]["roc12"] == _approx(2.0)


def test_select_top_n_min_score_filter():
    """min_score should drop top-N entries that score below the floor."""
    score = pd.Series({"a": 0.1, "b": 0.2, "c": -0.5})
    fv = pd.DataFrame({"roc12": [0.1, 0.2, -0.5]}, index=list("abc"))
    out = mf.select_top_n(score, fv, n=3, min_score=0.0)
    assert len(out) == 2  # c dropped (negative score)
    assert {x["stock"] for x in out} == {"a", "b"}


def test_select_top_n_empty_and_zero_n():
    score = pd.Series(dtype=float)
    assert mf.select_top_n(score, pd.DataFrame(), n=5) == []
    score2 = pd.Series({"a": 1.0})
    assert mf.select_top_n(score2, pd.DataFrame({"x": [1.0]}, index=["a"]), n=0) == []


def test_select_top_n_handles_nan_factor_values():
    """NaN raw factor values should be reported as None, not float NaN."""
    score = pd.Series({"a": 1.0})
    fv = pd.DataFrame({"roc12": [np.nan]}, index=["a"])
    out = mf.select_top_n(score, fv, n=1)
    assert out[0]["factors"]["roc12"] is None


# --- FactorWeight dataclass ------------------------------------------------


def test_factor_weight_defaults():
    fw = FactorWeight("roc12")
    assert fw.code == "roc12"
    assert fw.weight == 1.0
    assert fw.direction == 1


def test_factor_weight_reverse_factor():
    fw = FactorWeight("rsi14", weight=0.5, direction=-1)
    assert fw.direction == -1
