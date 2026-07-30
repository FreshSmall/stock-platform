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


# --------------------------------------------------------------------------
# V1.5 indicators: RSI, BOLL (EMA already exists above; reused by MACD).
# --------------------------------------------------------------------------


def calc_rsi(
    closes: pd.Series, periods: Iterable[int] = (6, 12, 24)
) -> pd.DataFrame:
    """Relative Strength Index (Wilder's smoothing).

    RSI = 100 - 100/(1 + RS),  RS = avg_gain / avg_loss, where the averages use
    Wilder's smoothing (EMA-like with alpha = 1/period). One ``rsi{p}`` column
    per period, aligned to ``closes`` with NaN for the leading ``p`` warmup bars.

    Convention matches 通达信/同花顺 (which also use Wilder smoothing). A flat or
    all-up series yields RSI -> 100 (no losses); the guard maps a zero avg_loss
    to RSI=100 to avoid div-by-zero.

    Args:
        closes: closing-price series.
        periods: windows (default 6/12/24).
    """
    closes = pd.Series(closes, dtype=float)
    delta = closes.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    cols = {}
    for p in periods:
        # Wilder smoothing: first avg = SMA over first `p` deltas; thereafter
        # an EMA with alpha = 1/p. We use ewm with com=p-1 (== alpha=1/p).
        avg_gain = gain.ewm(alpha=1.0 / p, adjust=False, min_periods=p).mean()
        avg_loss = loss.ewm(alpha=1.0 / p, adjust=False, min_periods=p).mean()
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        # Where there were no losses, RSI is defined as 100 (after warmup).
        rsi = rsi.where(avg_loss != 0, 100.0)
        cols[f"rsi{p}"] = rsi
    return pd.DataFrame(cols)


def calc_boll(
    closes: pd.Series, n: int = 20, k: int = 2
) -> pd.DataFrame:
    """Bollinger Bands.

    - ``boll_mid``  = SMA(close, n)
    - ``boll_up``   = mid + k * stdev(close, n)
    - ``boll_down`` = mid - k * stdev(close, n)

    stdev uses population stddev (ddof=0) to match the common A-share charting
    convention. Leading ``n - 1`` bars are NaN (rolling warmup).

    Args:
        closes: closing-price series.
        n: window (default 20).
        k: band width in stdevs (default 2).
    """
    closes = pd.Series(closes, dtype=float)
    mid = closes.rolling(window=n, min_periods=n).mean()
    std = closes.rolling(window=n, min_periods=n).std(ddof=0)
    up = mid + k * std
    down = mid - k * std
    return pd.DataFrame({"boll_mid": mid, "boll_up": up, "boll_down": down})
