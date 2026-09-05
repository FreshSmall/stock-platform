"""Factor effectiveness analysis: IC, IR, layered returns (BP-V2-013).

IC (Information Coefficient) = Spearman rank correlation between the factor
value and the forward ``horizon``-day return, computed across the stock
universe on a single rebalance date.

These are pure pandas/numpy functions — no DB access — so they're easy to unit
test on synthetic data. :mod:`factor_service` wires them to real factor values.
"""

from __future__ import annotations

import math

import pandas as pd
from scipy.stats import spearmanr


def forward_returns(
    prices: pd.DataFrame, horizon: int
) -> pd.Series:
    """Forward ``horizon``-day returns per stock.

    :param prices: DataFrame indexed by date, one column per stock (close).
    :param horizon: forward window in trading days.
    :return: Series of forward returns for the *last* rebalance date
        (ret = price[t+horizon] / price[t] - 1), one per stock.
    """
    if len(prices) <= horizon:
        return pd.Series(dtype=float)
    return (prices.iloc[-1 + horizon] / prices.iloc[-1] - 1.0).dropna()


def single_ic(
    factor_values: pd.Series, fwd_returns: pd.Series
) -> float | None:
    """Spearman IC between factor values and forward returns.

    Both Series are indexed by stock code. Returns None if fewer than 5 stocks
    overlap (IC is meaningless on a tiny universe).
    """
    common = factor_values.dropna().align(fwd_returns.dropna(), join="inner")[0]
    if len(common) < 5:
        return None
    fv_clean = factor_values.reindex(common.index).dropna()
    fr_clean = fwd_returns.reindex(fv_clean.index).dropna()
    if len(fv_clean) < 5:
        return None
    rho, _ = spearmanr(fv_clean, fr_clean)
    return None if math.isnan(rho) else float(rho)


def ic_series(
    factor_panel: pd.DataFrame, return_panel: pd.DataFrame, horizon: int
) -> pd.Series:
    """IC across many rebalance dates (for cumulative IC / IR).

    :param factor_panel: date × stock factor values.
    :param return_panel: date × stock forward returns (already shifted).
    :return: Series of per-date IC, indexed by rebalance date.
    """
    ics = {}
    for dt in factor_panel.index:
        if dt not in return_panel.index:
            continue
        ic = single_ic(factor_panel.loc[dt], return_panel.loc[dt])
        if ic is not None:
            ics[dt] = ic
    return pd.Series(ics, dtype=float)


def information_ratio(ic_seq: pd.Series) -> float | None:
    """IR = mean(IC) / std(IC). None if std is 0 or series too short."""
    if len(ic_seq) < 2:
        return None
    std = float(ic_seq.std())
    if std == 0:
        return None
    return float(ic_seq.mean() / std)


def layered_returns(
    factor_values: pd.Series, fwd_returns: pd.Series, n_layers: int = 5
) -> list[dict] | None:
    """Split stocks into ``n_layers`` quantiles by factor value; return each
    layer's mean forward return.

    :return: list of ``{layer, mean_return, count}`` (layer 1 = lowest factor),
        or None if too few stocks.
    """
    common = factor_values.dropna().align(fwd_returns.dropna(), join="inner")[0]
    if len(common) < n_layers:
        return None
    fv = factor_values.reindex(common.index).dropna()
    fr = fwd_returns.reindex(fv.index)
    # pd.qcut with duplicate edges can fail; fall back to rank-based binning.
    try:
        bins = pd.qcut(fv, n_layers, labels=False, duplicates="drop")
    except Exception:
        bins = pd.Series(pd.cut(fv.rank(), n_layers, labels=False), index=fv.index)
    out = []
    n_actual = int(bins.max()) + 1 if not bins.isna().all() else 0
    for layer in range(n_actual):
        mask = bins == layer
        layer_ret = fr[mask]
        out.append(
            {
                "layer": layer + 1,
                "mean_return": round(float(layer_ret.mean()), 6) if not layer_ret.empty else None,
                "count": int(layer_ret.count()),
            }
        )
    return out
