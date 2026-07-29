"""Technical indicator calculations (pure pandas/numpy, no TA-Lib).

These are the pure-function core used by the strategy layer and exposed via
the ``/stock/{code}/indicators`` API. Each function takes a pandas Series
(or numpy-friendly iterable) and returns a frame/series aligned to the input
index, with NaN used for leading warmup values where the math is undefined.

The MVP deliberately avoids TA-Lib: it is a C extension that is awkward to
build on macOS/arm64 and not currently installed. pandas/numpy reproduce the
standard A-share conventions closely enough for screening-grade output.
"""

from typing import Iterable

import numpy as np
import pandas as pd


def calc_ma(closes: pd.Series, periods: Iterable[int] = (5, 10, 20)) -> pd.DataFrame:
    """Simple Moving Averages.

    Args:
        closes: closing-price series (any orderable index).
        periods: windows to compute, e.g. ``(5, 10, 20)``.

    Returns:
        DataFrame with one ``ma{p}`` column per period, aligned to ``closes``
        with NaN for the leading ``p - 1`` bars of each window.
    """
    closes = pd.Series(closes, dtype=float)
    return pd.DataFrame(
        {f"ma{p}": closes.rolling(window=p, min_periods=p).mean() for p in periods}
    )


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average (``adjust=False`` → recursive seeding).

    ``adjust=False`` matches the convention used by most Chinese charting
    software (e.g. 通达信/同花顺): the first value seeds the recursion rather
    than being length-weighted.
    """
    return pd.Series(series, dtype=float).ewm(span=period, adjust=False).mean()


def calc_macd(
    closes: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    """MACD indicator (standard 12/26/9 A-share params).

    - ``dif``  (MACD line)   = EMA_fast - EMA_slow
    - ``dea``  (signal line) = EMA(dif, signal)
    - ``macd`` (histogram)   = (dif - dea) * 2

    Returns:
        DataFrame with columns ``dif``, ``dea``, ``macd``. Because EMA
        (``adjust=False``) is defined from the very first bar, there is no
        NaN warmup — the leading values are simply less reliable.
    """
    closes = pd.Series(closes, dtype=float)
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)
    dif = ema_fast - ema_slow
    dea = calc_ema(dif, signal)
    macd_hist = (dif - dea) * 2
    return pd.DataFrame({"dif": dif, "dea": dea, "macd": macd_hist})


def calc_kdj(
    highs: pd.Series, lows: pd.Series, closes: pd.Series, n: int = 9
) -> pd.DataFrame:
    """KDJ indicator (classic 9-period).

    RSV = (close - lowest_low_n)  / (highest_high_n - lowest_low_n) * 100
    K_t = K_{t-1} * 2/3 + RSV_t / 3        (recursive SMA-style smoothing)
    D_t = D_{t-1} * 2/3 + K_t   / 3
    J   = 3*K - 2*D

    The K/D seeds are 50 (the standard convention). ``rolling`` uses
    ``min_periods=1`` so RSV is defined from the first bar; the recursion
    therefore produces K/D in roughly ``[0, 100]`` after the first few bars
    converge toward the true range. J is unbounded and may overshoot.

    Args:
        highs/lows/closes: aligned OHLC series.
        n: RSV lookback window (default 9).

    Returns:
        DataFrame with columns ``k``, ``d``, ``j``.
    """
    highs = pd.Series(highs, dtype=float)
    lows = pd.Series(lows, dtype=float)
    closes = pd.Series(closes, dtype=float)
    lowest_low = lows.rolling(window=n, min_periods=1).min()
    highest_high = highs.rolling(window=n, min_periods=1).max()
    rsv = (closes - lowest_low) / (highest_high - lowest_low) * 100

    # Recursive smoothing: K_t = K_{t-1}*2/3 + RSV_t/3, seeded at 50.
    k = pd.Series(np.nan, index=closes.index, dtype=float)
    d = pd.Series(np.nan, index=closes.index, dtype=float)
    k_prev = 50.0
    d_prev = 50.0
    for i in range(len(closes)):
        rsv_i = rsv.iloc[i]
        if np.isnan(rsv_i):
            # Defensive: keep NaN in sync with RSV (e.g. flat highs==lows gap).
            k.iloc[i] = np.nan
            d.iloc[i] = np.nan
            continue
        k_prev = k_prev * 2.0 / 3.0 + rsv_i / 3.0
        d_prev = d_prev * 2.0 / 3.0 + k_prev / 3.0
        k.iloc[i] = k_prev
        d.iloc[i] = d_prev
    j = 3 * k - 2 * d
    return pd.DataFrame({"k": k, "d": d, "j": j})


def golden_cross(fast: pd.Series, slow: pd.Series) -> pd.Series:
    """Boolean Series: True where ``fast`` crosses ABOVE ``slow``.

    A bar ``t`` is a golden cross iff ``fast[t] > slow[t]`` and
    ``fast[t-1] <= slow[t-1]``.
    """
    fast = pd.Series(fast).reset_index(drop=True)
    slow = pd.Series(slow).reset_index(drop=True)
    prev_le = fast.shift(1) <= slow.shift(1)
    now_gt = fast > slow
    return (prev_le & now_gt).fillna(False)


def death_cross(fast: pd.Series, slow: pd.Series) -> pd.Series:
    """Boolean Series: True where ``fast`` crosses BELOW ``slow``.

    A bar ``t`` is a death cross iff ``fast[t] < slow[t]`` and
    ``fast[t-1] >= slow[t-1]``.
    """
    fast = pd.Series(fast).reset_index(drop=True)
    slow = pd.Series(slow).reset_index(drop=True)
    prev_ge = fast.shift(1) >= slow.shift(1)
    now_lt = fast < slow
    return (prev_ge & now_lt).fillna(False)
