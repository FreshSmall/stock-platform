"""Feature engineering for the meta-model — strictly ≤ signal day.

The per-stock indicator frame (:func:`indicator_frame`) is computed once per
stock (vectorised) and indexed at each signal's ``t``. Nothing here may
reference bars after ``t`` — the label side (barrier outcome) lives in
:mod:`app.ml.barriers` and must stay time-separated from these features.
"""

import numpy as np
import pandas as pd

# Feature order is part of the model contract (train/predict columns).
FEATS = [
    "slope_norm",  # trendline slope, normalised by price
    "dev",  # breakout magnitude: close vs trendline
    "adx14",  # trend strength: breakouts in trending markets are more reliable
    "atr_pct",  # volatility regime
    "vol_ratio",  # volume vs 20d mean: confirmed vs unconfirmed breakouts
    "rsi12",
    "dist_ma20",  # close vs MA20
    "ret5",  # 5-day momentum into the breakout
    "ll_span",  # bars between the two pivot lows (trendline quality)
    "bars_since",  # bars from the last pivot low to the breakout
    "log_amt20",  # liquidity level: log 20d avg notional (volume×close proxy)
    "idx_ret20",  # market regime: 20d return of the equal-weight market index
]


def vec_rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI on [0, 100]. All-gain windows → 100 (avg loss 0)."""
    delta = close.astype(float).diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return rsi.mask(avg_loss == 0, 100.0)


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].astype(float).shift(1)
    return pd.concat(
        [
            df["high"].astype(float) - df["low"].astype(float),
            (df["high"].astype(float) - prev_close).abs(),
            (df["low"].astype(float) - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def vec_atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder ATR."""
    return _true_range(df).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def vec_adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder ADX on [0, 100]; NaN while the smoothed DI+DI- sum is 0."""
    up = df["high"].astype(float).diff()
    dn = -df["low"].astype(float).diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)

    def smooth(s: pd.Series) -> pd.Series:
        return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()

    atr = smooth(_true_range(df)).replace(0.0, np.nan)
    plus_di = 100.0 * smooth(plus_dm) / atr
    minus_di = 100.0 * smooth(minus_dm) / atr
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return smooth(dx)


def indicator_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Rolling indicators shared by all signals of one stock (compute once)."""
    close, vol = df["close"].astype(float), df["volume"].astype(float)
    # Notional proxy: volume (手) × 100 × close — uniform across sources even
    # where ``amount`` is NULL (the Tencent primary source omits it), unlike
    # the raw ``amount`` column which is only populated on alternate-host /
    # eastmoney rows.
    notional = vol * 100.0 * close
    return pd.DataFrame(
        {
            "ma20": close.rolling(20).mean(),
            "vol_ma20": vol.rolling(20).mean(),
            "amt_ma20": notional.rolling(20).mean(),
            "rsi12": vec_rsi(close, 12),
            "atr14": vec_atr(df, 14),
            "adx14": vec_adx(df, 14),
        },
        index=df.index,
    )


def build_features(
    df: pd.DataFrame,
    ind: pd.DataFrame,
    sig: dict,
    mkt_ret20: pd.Series | None = None,
) -> dict:
    """One feature row for signal ``sig`` at positional index ``t``.

    ``ind`` is :func:`indicator_frame` output for the same ``df``;
    ``mkt_ret20`` is an optional date-indexed 20-day market-return series
    (:func:`app.services.meta_label_service.market_series` output). Missing
    history (early signals / warm-up) yields NaN, dropped downstream.
    """
    t = int(sig["t"])
    close = float(df["close"].iloc[t])
    ma20 = float(ind["ma20"].iloc[t])
    vol_ma20 = float(ind["vol_ma20"].iloc[t])
    amt_ma20 = float(ind["amt_ma20"].iloc[t])
    mkt = (
        float(mkt_ret20.loc[sig["trade_date"]])
        if mkt_ret20 is not None and sig["trade_date"] in mkt_ret20.index
        else np.nan
    )
    return {
        "slope_norm": sig["slope"] / close,
        "dev": sig["dev"],
        "adx14": float(ind["adx14"].iloc[t]),
        "atr_pct": float(ind["atr14"].iloc[t]) / close,
        "vol_ratio": float(df["volume"].iloc[t]) / vol_ma20 - 1.0,
        "rsi12": float(ind["rsi12"].iloc[t]),
        "dist_ma20": close / ma20 - 1.0,
        "ret5": close / float(df["close"].iloc[t - 5]) - 1.0 if t >= 5 else np.nan,
        "ll_span": int(sig["i2"]) - int(sig["i1"]),
        "bars_since": t - int(sig["i2"]),
        "log_amt20": np.log10(amt_ma20) if amt_ma20 > 0 else np.nan,
        "idx_ret20": mkt,
    }
