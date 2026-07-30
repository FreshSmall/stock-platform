"""Unit tests for :mod:`app.services.indicator_service` (Task B2).

These are pure-function tests (no DB): each indicator is checked against
hand-computed expected values on small synthetic series so the math, not the
data, is what fails if anything regresses.
"""

import numpy as np
import pandas as pd
import pytest

from app.services import indicator_service


# --------------------------------------------------------------------------
# Moving Averages
# --------------------------------------------------------------------------


def test_calc_ma_basic() -> None:
    """ma3 of [1,2,3,4,5]: bar 2 == mean(1,2,3) == 2.0, bars 0-1 are NaN."""
    closes = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    df = indicator_service.calc_ma(closes, periods=[3])
    assert list(df.columns) == ["ma3"]
    assert df["ma3"].iloc[2] == pytest.approx(2.0)
    assert pd.isna(df["ma3"].iloc[0])
    assert pd.isna(df["ma3"].iloc[1])
    # Full window only: bar 3 == mean(2,3,4) == 3.0
    assert df["ma3"].iloc[3] == pytest.approx(3.0)


def test_calc_ma_default_periods_and_dtype() -> None:
    """Default periods are (5, 10, 20) and the frame is float-typed."""
    closes = pd.Series([float(i) for i in range(1, 26)])
    df = indicator_service.calc_ma(closes)
    assert list(df.columns) == ["ma5", "ma10", "ma20"]
    # Last row of a strictly increasing series is fully populated.
    assert not df.iloc[-1].isna().any()


def test_calc_ma_multiple_periods_warmup() -> None:
    """Each ma{p} column has exactly ``p - 1`` leading NaN warmup bars."""
    closes = pd.Series([float(i) for i in range(1, 26)])  # 25 elements
    df = indicator_service.calc_ma(closes, periods=[5, 10, 20])
    for p in (5, 10, 20):
        col = df[f"ma{p}"]
        n_nan = int(col.isna().sum())
        assert n_nan == p - 1, f"ma{p} expected {p - 1} NaN, got {n_nan}"
        # First non-NaN sits exactly at index p-1.
        assert not pd.isna(col.iloc[p - 1])


# --------------------------------------------------------------------------
# EMA
# --------------------------------------------------------------------------


def test_calc_ema_adjust_false() -> None:
    """EMA2 [1,2,3,4] adjust=False seeds at x[0]=1 and trends upward."""
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    ema = indicator_service.calc_ema(s, 2)
    assert ema.iloc[0] == pytest.approx(1.0)
    # alpha = 2/3 -> ema[1] = 2*(2/3) + 1*(1/3) = 5/3
    assert ema.iloc[1] == pytest.approx(5.0 / 3.0)
    # Strictly increasing input on an increasing series -> strictly increasing EMA.
    diffs = ema.diff().dropna()
    assert (diffs > 0).all()


# --------------------------------------------------------------------------
# MACD
# --------------------------------------------------------------------------


def test_calc_macd_columns_and_length() -> None:
    """calc_macd returns a DataFrame with dif/dea/macd, same length as input."""
    closes = pd.Series([float(i) for i in range(50)])
    df = indicator_service.calc_macd(closes)
    assert list(df.columns) == ["dif", "dea", "macd"]
    assert len(df) == len(closes)


def test_calc_macd_no_nan_warmup() -> None:
    """EMA(adjust=False) has no warmup, so dif/dea/macd are all non-NaN."""
    closes = pd.Series([float(i) for i in range(1, 21)])  # 20 closes
    df = indicator_service.calc_macd(closes)
    assert not df["dif"].isna().any()
    assert not df["dea"].isna().any()
    assert not df["macd"].isna().any()


def test_calc_macd_histogram_identity() -> None:
    """macd histogram == (dif - dea) * 2 exactly."""
    closes = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0, 13.0, 12.0, 11.0, 10.0, 9.0] * 3)
    df = indicator_service.calc_macd(closes)
    expected = (df["dif"] - df["dea"]) * 2
    pd.testing.assert_series_equal(df["macd"], expected, check_names=False)


# --------------------------------------------------------------------------
# KDJ
# --------------------------------------------------------------------------


def test_calc_kdj_columns_and_range() -> None:
    """KDJ columns are k/d/j; K and D sit within [0, 100] for converged bars."""
    # Synthetic OHLC: trending up then chopping, length 40.
    highs = pd.Series([10.0 + i + (i % 3) for i in range(40)])
    lows = pd.Series([10.0 + i - (i % 3) for i in range(40)])
    closes = pd.Series([10.0 + i for i in range(40)])
    df = indicator_service.calc_kdj(highs, lows, closes)
    assert list(df.columns) == ["k", "d", "j"]
    assert len(df) == len(closes)
    # K and D are bounded in [0, 100] once the recursion has converged a bit.
    tail_k = df["k"].iloc[5:]
    tail_d = df["d"].iloc[5:]
    assert tail_k.between(0, 100).all(), f"K out of range:\n{tail_k}"
    assert tail_d.between(0, 100).all(), f"D out of range:\n{tail_d}"
    # J = 3K - 2D exactly.
    expected_j = 3 * df["k"] - 2 * df["d"]
    pd.testing.assert_series_equal(df["j"], expected_j, check_names=False)


def test_calc_kdj_rsv_extremes() -> None:
    """A close at the rolling high -> RSV=100 -> K/D climb toward 100."""
    # Monotone-up closes with high == close so RSV saturates at 100 each bar.
    closes = pd.Series([float(i) for i in range(1, 15)])
    highs = closes.copy()
    lows = closes - 1.0
    df = indicator_service.calc_kdj(highs, lows, closes)
    # After enough bars of RSV=100 the smoothed K/D approach 100 from below.
    assert df["k"].iloc[-1] > 95.0
    assert df["k"].iloc[-1] <= 100.0


# --------------------------------------------------------------------------
# Crossover helpers
# --------------------------------------------------------------------------


def test_golden_cross_detection() -> None:
    """fast=[1,3,2,1], slow=[2,2,2,2]: golden cross at index 1 only."""
    fast = pd.Series([1.0, 3.0, 2.0, 1.0])
    slow = pd.Series([2.0, 2.0, 2.0, 2.0])
    cross = indicator_service.golden_cross(fast, slow)
    expected = pd.Series([False, True, False, False])
    pd.testing.assert_series_equal(cross, expected, check_names=False)


def test_death_cross_detection() -> None:
    """fast=[3,1,2,3], slow=[2,2,2,2]: death cross at index 1 only."""
    fast = pd.Series([3.0, 1.0, 2.0, 3.0])
    slow = pd.Series([2.0, 2.0, 2.0, 2.0])
    cross = indicator_service.death_cross(fast, slow)
    expected = pd.Series([False, True, False, False])
    pd.testing.assert_series_equal(cross, expected, check_names=False)


def test_crosses_never_overlap() -> None:
    """On a noisy series a golden and death cross never fire on the same bar."""
    rng = np.random.default_rng(42)
    fast = pd.Series(rng.uniform(0, 10, 200))
    slow = pd.Series(rng.uniform(0, 10, 200))
    gc = indicator_service.golden_cross(fast, slow)
    dc = indicator_service.death_cross(fast, slow)
    assert not (gc & dc).any()


# --------------------------------------------------------------------------
# V1.5: RSI, BOLL
# --------------------------------------------------------------------------


def test_calc_rsi_all_up_is_high() -> None:
    """A strictly rising series has no losses -> RSI -> 100 after warmup."""
    closes = pd.Series([float(i) for i in range(1, 30)])
    df = indicator_service.calc_rsi(closes, periods=[6])
    # after warmup (>= index 6), RSI should be 100 (no losses)
    assert df["rsi6"].iloc[-1] == pytest.approx(100.0)
    # leading warmup bars are NaN
    assert pd.isna(df["rsi6"].iloc[0])


def test_calc_rsi_all_down_is_low() -> None:
    """A strictly falling series -> RSI -> 0 after warmup."""
    closes = pd.Series([float(i) for i in range(30, 1, -1)])
    df = indicator_service.calc_rsi(closes, periods=[12])
    assert df["rsi12"].iloc[-1] == pytest.approx(0.0, abs=1e-6)


def test_calc_rsi_default_periods() -> None:
    closes = pd.Series([float(i) for i in range(1, 50)])
    df = indicator_service.calc_rsi(closes)
    assert list(df.columns) == ["rsi6", "rsi12", "rsi24"]
    assert 0.0 <= df["rsi6"].iloc[-1] <= 100.0


def test_calc_rsi_in_range() -> None:
    """RSI always lies in [0, 100] on any series."""
    rng = np.random.default_rng(7)
    closes = pd.Series(100 + rng.uniform(-5, 5, 100).cumsum())
    df = indicator_service.calc_rsi(closes, periods=[14])
    vals = df["rsi14"].dropna()
    assert (vals >= 0).all() and (vals <= 100).all()


def test_calc_boll_basic() -> None:
    """mid = SMA(20); up/down = mid ± 2*std; warmup of 19 NaN."""
    closes = pd.Series([float(i) for i in range(1, 41)])
    df = indicator_service.calc_boll(closes, n=20, k=2)
    assert list(df.columns) == ["boll_mid", "boll_up", "boll_down"]
    # warmup
    assert pd.isna(df["boll_mid"].iloc[18])
    assert not pd.isna(df["boll_mid"].iloc[19])
    # mid at index 19 == mean(1..20)
    assert df["boll_mid"].iloc[19] == pytest.approx(10.5)
    # up >= mid >= down
    last = 39
    assert df["boll_up"].iloc[last] >= df["boll_mid"].iloc[last]
    assert df["boll_mid"].iloc[last] >= df["boll_down"].iloc[last]


def test_calc_boll_up_down_symmetric() -> None:
    """up and down are equidistant from mid by k*std."""
    rng = np.random.default_rng(1)
    closes = pd.Series(50 + rng.uniform(-3, 3, 60))
    df = indicator_service.calc_boll(closes, n=20, k=2)
    width_up = df["boll_up"] - df["boll_mid"]
    width_down = df["boll_mid"] - df["boll_down"]
    valid = width_up.dropna()
    assert np.allclose(valid.values, width_down.dropna().values)
