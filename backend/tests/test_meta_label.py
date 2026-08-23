"""Tests for the meta-labeling pipeline (pure functions, no DB).

Mirrors the style of test_factor_multi.py: synthetic pandas frames, one
behaviour per test. The anti-lookahead properties (pivot confirmation lag,
purged walk-forward) are asserted explicitly — they are the whole point of
this pipeline.
"""

import numpy as np
import pandas as pd

from app.ml import backtest as bt
from app.ml import barriers, features, model, trendline
from app.services.meta_label_service import build_signal_frame, market_series


def test_market_series_tracks_median_of_two_stocks():
    """Equal-weight level: median daily return compounds into the level."""
    up = _bars([100.0 * 1.01**i for i in range(5)])   # exactly +1%/day
    flat = _bars([50.0] * 5)                          # 0%/day
    level = market_series({"u": up, "f": flat})
    # median of (+1%, 0%) = +0.5%/day, from day 2 on
    assert abs(level.iloc[-1] - 1.005**4) < 1e-9


def _bars(
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    opens: list[float] | None = None,
    vols: list[float] | None = None,
    n: int | None = None,
) -> pd.DataFrame:
    """A synthetic OHLCV frame; unspecified fields get quiet defaults."""
    k = n if n is not None else len(closes)
    return pd.DataFrame(
        {
            "trade_date": pd.date_range("2024-01-01", periods=k),
            "open": opens if opens is not None else [closes[0]] + closes[:-1],
            "high": highs if highs is not None else [c + 0.5 for c in closes],
            "low": lows if lows is not None else [c - 0.5 for c in closes],
            "close": closes,
            "volume": vols if vols is not None else [1_000_000.0] * k,
        }
    )


def _walk_frame(seed: int = 11, n: int = 800) -> pd.DataFrame:
    """Random-walk closes with noisy low/high offsets around them.

    The noisy offsets matter: a constant ``low = close - d`` pins the
    trendline structurally below every close, so no undercut→reclaim (signal)
    can ever fire. Offsets drawn per bar let closes genuinely dip below the
    line through the pivot lows.
    """
    rng = np.random.default_rng(seed)
    closes = 100 + np.cumsum(rng.normal(0, 1.5, n))
    lows = closes - np.abs(rng.normal(0, 0.4, n))
    highs = closes + np.abs(rng.normal(0, 0.4, n))
    return _bars(
        list(closes),
        highs=list(highs),
        lows=list(lows),
        vols=list(1e6 * (1 + rng.random(n))),
    )


# --- pivot detection -------------------------------------------------------


def test_pivot_lows_finds_unique_minima_with_lag():
    """Two V-bottoms → pivots at the troughs; flat segments yield none."""
    lows = pd.Series([5, 4, 3, 2, 1, 2, 3, 4, 5, 4, 3, 2, 3, 4, 5], dtype=float)
    assert trendline.pivot_lows(lows, order=3) == [(4, 1.0), (11, 2.0)]
    # the pivot at 4 needs bars up to 7 to confirm; at 11, up to 14 — the list
    # is "observable at end of series", callers enforce the per-bar lag.


def test_pivot_lows_ignores_ties():
    lows = pd.Series([3, 3, 3, 3, 3, 3, 3], dtype=float)
    assert trendline.pivot_lows(lows, order=3) == []


# --- trendline signals -----------------------------------------------------


def _craft_breakout_frame(n: int = 60) -> pd.DataFrame:
    """Flat closes at 10, two pivot lows at 8 (9.0) and 20 (10.0), breakout at 30.

    The line through the pivots has slope 1/12 and value 10.83 at bar 30, so
    close[29]=10 (below) and close[30]=11 (above) make bar 30 the first cross.
    """
    closes = [10.0] * n
    closes[30] = 11.0
    lows = [12.0] * n
    lows[8], lows[20] = 9.0, 10.0
    highs = [11.5] * n
    return _bars(closes, highs=highs, lows=lows)


def test_signal_fires_on_first_cross_only():
    sigs = trendline.trendline_signals(_craft_breakout_frame(), order=3)
    assert len(sigs) == 1
    row = sigs.iloc[0]
    assert row["t"] == 30
    assert row["i1"] == 8 and row["i2"] == 20
    assert row["slope"] > 0 and row["dev"] > 0


def test_signals_never_use_unconfirmed_pivots():
    """Property test: every signal's last pivot was confirmed by t-1."""
    sigs = trendline.trendline_signals(_walk_frame(seed=11), order=3)
    assert len(sigs) >= 10
    assert all(sigs["i2"] + 3 <= sigs["t"] - 1)
    assert (sigs["slope"] > 0).all() and (sigs["dev"] > 0).all()


# --- triple barrier --------------------------------------------------------


def test_barrier_upper_touch_labels_true():
    df = _bars([100.0] * 12, highs=[100.5] * 3 + [104.5] + [100.5] * 8)
    lab = barriers.triple_barrier(df, 0, pt=0.04, sl=0.02, horizon=10)
    assert lab["label"] == 1
    assert abs(lab["ret"] - (0.04 - barriers.COST)) < 1e-9
    assert lab["hold"] == 2  # entered at bar 1, exited at bar 3


def test_barrier_lower_touch_labels_false():
    df = _bars([100.0] * 12, lows=[99.5, 99.5, 97.5] + [99.5] * 9)
    lab = barriers.triple_barrier(df, 0, pt=0.04, sl=0.02, horizon=10)
    assert lab["label"] == 0
    assert abs(lab["ret"] - (-0.02 - barriers.COST)) < 1e-9
    assert lab["hold"] == 1


def test_barrier_same_day_double_touch_resolves_as_stop():
    df = _bars(
        [100.0] * 12,
        highs=[100.5, 100.5, 104.5] + [100.5] * 9,
        lows=[99.5, 99.5, 97.5] + [99.5] * 9,
    )
    lab = barriers.triple_barrier(df, 0, pt=0.04, sl=0.02, horizon=10)
    assert lab["label"] == 0  # conservative


def test_barrier_vertical_expiry_labels_by_return_sign():
    quiet = _bars([100.0] * 12)  # nothing touched; exit close == entry → 0
    assert barriers.triple_barrier(quiet, 0, horizon=10)["label"] == 0
    up = _bars([100.0] * 10 + [102.0, 102.0])  # close-only exit above entry → 1
    assert barriers.triple_barrier(up, 0, horizon=10)["label"] == 1


def test_barrier_drops_unfillable_gap_and_tail_signals():
    gapped = _bars([100.0] * 12, opens=[100.0, 110.0] + [100.0] * 10)
    assert barriers.triple_barrier(gapped, 0) is None  # ~limit-up open
    assert barriers.triple_barrier(_bars([100.0] * 12), 11) is None  # no next bar


def test_barrier_atr_mode_scales_widths():
    """atr_pct=0.02 with 2×/1× multipliers reproduces the fixed 4%/2% widths."""
    df = _bars([100.0] * 12, highs=[100.5] * 3 + [104.5] + [100.5] * 8)
    lab = barriers.triple_barrier(df, 0, horizon=10, atr_pct=0.02)
    assert lab["label"] == 1
    assert abs(lab["ret"] - (0.04 - barriers.COST)) < 1e-9

    # wider ATR → wider barriers: a 5% spike no longer touches the +6% target
    df2 = _bars([100.0] * 12, highs=[100.5] * 3 + [105.0] + [100.5] * 8)
    lab2 = barriers.triple_barrier(df2, 0, horizon=10, atr_pct=0.03)
    assert lab2["label"] == 0  # vertical expiry below the untouchable barrier


# --- indicators ------------------------------------------------------------


def test_vector_indicators_stay_in_range():
    rng = np.random.default_rng(3)
    closes = list(100 + np.cumsum(rng.normal(0, 1.0, 150)))
    df = _bars(closes)
    ind = features.indicator_frame(df)
    rsi, adx, atr = ind["rsi12"].dropna(), ind["adx14"].dropna(), ind["atr14"].dropna()
    assert not rsi.empty and rsi.between(0, 100).all()
    assert not adx.empty and adx.between(0, 100).all()
    assert not atr.empty and (atr > 0).all()


# --- purged walk-forward ---------------------------------------------------


def _signal_frame(n: int = 120, noise: float = 0.0, seed: int = 11) -> pd.DataFrame:
    """Signals whose label is driven by the first FEATS column (plus flips).

    Columns follow the model contract (:data:`app.ml.features.FEATS`) — the
    forest trains on those exact names.
    """
    rng = np.random.default_rng(seed)
    x = rng.normal(0, 1, n)
    labels = (x > 0).astype(int)
    if noise:
        flip = rng.random(n) < noise
        labels = np.where(flip, 1 - labels, labels)
    dates = pd.date_range("2020-01-01", periods=n, freq="D")
    data = {f: rng.normal(0, 1, n) for f in features.FEATS}
    data[features.FEATS[0]] = x
    return pd.DataFrame(
        {"trade_date": dates, "t_end_date": dates + pd.Timedelta(days=5), "label": labels, **data}
    )


def test_purge_drops_barrier_windows_invading_test_period():
    frame = pd.DataFrame(
        {
            "t_end_date": pd.to_datetime(
                ["2024-01-01", "2024-01-05", "2023-12-20", "2023-12-28"]
            )
        }
    )
    kept = model.purge(frame, pd.Timestamp("2024-01-10"), embargo_days=14)
    # cutoff = 2023-12-27: only barriers fully ending before it survive
    assert list(kept["t_end_date"].dt.day) == [20]


def test_walk_forward_scores_every_test_fold():
    pred, clf = model.walk_forward(
        _signal_frame(120),
        init_train=40,
        step=20,
        rf_overrides={"n_estimators": 100, "min_samples_leaf": 5},
    )
    assert len(pred) == 80  # folds at 40/60/80/100
    assert clf is not None
    assert pred["prob"].between(0, 1).all()


def test_walk_forward_learns_separable_signal_out_of_sample():
    """The referee must rank true breakouts above false ones OOS."""
    frame = _signal_frame(200, noise=0.05)
    pred, _ = model.walk_forward(
        frame,
        init_train=60,
        step=20,
        rf_overrides={"n_estimators": 200, "min_samples_leaf": 5},
    )
    acc = ((pred["prob"] > 0.5).astype(int) == pred["label"]).mean()
    assert acc > 0.75
    assert pred.loc[pred["label"] == 1, "prob"].mean() > pred.loc[pred["label"] == 0, "prob"].mean()


# --- performance summary ---------------------------------------------------


def test_perf_stats_on_known_trades():
    trades = pd.DataFrame(
        {
            "trade_date": pd.date_range("2024-01-01", periods=4),
            "ret": [0.10, -0.05, 0.10, -0.05],
            "hold": [1, 2, 3, 4],
        }
    )
    stats = bt.perf(trades)
    assert stats["n_trades"] == 4
    assert stats["win_rate"] == 0.5
    assert stats["profit_factor"] == 2.0
    assert abs(stats["avg_ret"] - 0.025) < 1e-9
    assert stats["max_drawdown"] == -0.05  # 1.045/1.1 and 1.092/1.1495 both -5%
    assert stats["avg_hold_days"] == 2.5


def test_equity_curve_downsamples_but_preserves_growth():
    trades = pd.DataFrame(
        {
            "trade_date": pd.date_range("2024-01-01", periods=500),
            "ret": [0.01] * 500,
            "hold": [1] * 500,
        }
    )
    curve = bt.equity_curve(trades, points=100)
    assert len(curve) == 100
    assert curve[-1] > curve[0] > 1.0


# --- pipeline wiring -------------------------------------------------------


def _wavy_frame(n: int = 600) -> pd.DataFrame:
    """Drifting two-harmonic sine — enough amplitude to undercut its own
    trendline and reclaim it (see :func:`_walk_frame` for why lows can't sit
    at a constant offset below closes)."""
    t = np.arange(n)
    closes = 100 + 0.05 * t + 3.0 * np.sin(t / 5.0) + 6.0 * np.sin(t / 17.0)
    return _bars(
        list(closes),
        highs=list(closes + 0.3),
        lows=list(closes - 0.3),
        vols=list(1e6 * (1 + 0.5 * np.sin(t / 3.0))),
    )


def test_build_signal_frame_wires_labels_and_features():
    frame = build_signal_frame({"000001": _wavy_frame()}, horizon=10)
    assert not frame.empty
    assert set(frame["label"].unique()) <= {0, 1}
    for feat in features.FEATS:
        assert np.isfinite(frame[feat]).all(), feat
    # label/return consistency: label 1 ⇒ positive pre-cost return
    gross = frame["ret"] + barriers.COST
    assert (gross[frame["label"] == 1] > 0).all()
