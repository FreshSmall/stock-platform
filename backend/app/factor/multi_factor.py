"""Multi-factor scoring & stock selection (横截面选股).

A *multi-factor model* scores every stock in a universe on a common scale by
combining several factor values into a single composite score, then picks the
top-N as the target portfolio. This is the workhorse of most A-share quant
funds (指数增强 / 量化中性 both reduce to "score the universe, hold the top").

Pipeline (see :func:`score_stocks`):

    for each (factor, stock) -> raw factor value          [Factor.compute]
    -> z-score normalise per factor (cross-sectional)     [_zscore]
    -> clip to [-3, 3] to tame outliers                   [_zscore]
    -> multiply by signed weight, sum across factors      [composite_score]
    -> rank, return top-N with per-factor breakdown        [select_top_n]

Design notes
------------
* **Pure functions first.** :func:`composite_score` / :func:`select_top_n`
  take a wide DataFrame and return a Series/list — no DB, fully unit-testable,
  mirroring the style of :mod:`app.factor.ic`.
* **Missing factors are imputed to the median**, not dropped. A stock that
  lacks one factor shouldn't be ejected outright — it just gets a neutral
  score on that factor. Median (not mean) is robust to the same outliers that
  motivate the clip.
* **Weights are signed.** Pass a negative weight for reverse factors (e.g.
  RSI14 entered long-only is often negated: high RSI = overbought = avoid).
  Equivalently pass ``direction=-1`` per factor.
* **Direction is applied before weighting**, so callers can keep all weights
  positive and express "want low RSI" via ``direction=-1`` on that factor —
  clearer than negative weights for non-quants reading the config.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.factor.base import registry as factor_registry

logger = logging.getLogger(__name__)


@dataclass
class FactorWeight:
    """One factor's contribution config to the composite score.

    :param code: registered factor code, e.g. ``"rsi14"`` / ``"roc12"``.
    :param weight: non-negative importance. Normalised internally so the
        caller need not make weights sum to 1.
    :param direction: +1 = prefer high factor values (momentum/ROC),
        -1 = prefer low values (RSI when overbought is bad, HV when you
        want low-vol stocks). Default +1.
    """

    code: str
    weight: float = 1.0
    direction: int = 1


# ---------------------------------------------------------------------------
# Pure core: normalisation + scoring. No DB access — easy to unit test.
# ---------------------------------------------------------------------------


def _zscore(series: pd.Series, clip: float = 3.0) -> pd.Series:
    """Standardise ``series`` to mean-0 / std-1, then clip to ``[-clip, clip]``.

    Clipping is essential: a single 10σ outlier (e.g. a freshly-listed stock
    with a 200% ROC) would otherwise dominate the whole cross-section and make
    every other stock look identical. Industry practice is to clip at ±3σ.

    Returns an all-zero Series if the input has zero variance (every stock the
    same factor value) — a flat factor carries no information either way.
    """
    s = series.astype(float)
    mu = s.mean()
    sigma = s.std(ddof=0)
    if sigma == 0 or pd.isna(sigma):
        return pd.Series(0.0, index=s.index)
    z = (s - mu) / sigma
    return z.clip(-clip, clip)


def composite_score(
    factor_values: pd.DataFrame,
    weights: dict[str, float],
    directions: dict[str, int] | None = None,
) -> pd.Series:
    """Combine per-factor z-scores into one composite score per stock.

    :param factor_values: DataFrame indexed by stock code, one column per
        factor code (raw values, will be z-scored internally).
    :param weights: ``{factor_code: importance}``. Need not sum to 1; they're
        normalised to a unit sum so the final score stays on a stable scale.
    :param directions: ``{factor_code: +1|-1}``. Omitted factors default to
        +1 (prefer high). -1 flips the sign so "prefer low" stocks win.
    :return: Series of composite scores, indexed by stock, descending-sorted.

    Missing values in a column are imputed to that column's median *before*
    z-scoring — see module docstring for the rationale.
    """
    directions = directions or {}
    if factor_values.empty:
        return pd.Series(dtype=float)

    # Restrict to factors the caller actually asked for AND that we have data
    # for. Unknown factor codes are silently dropped (logged by the caller).
    usable_cols = [c for c in weights if c in factor_values.columns]
    if not usable_cols:
        return pd.Series(dtype=float)

    parts: list[pd.Series] = []
    for code in usable_cols:
        col = factor_values[code]
        # Median imputation keeps the cross-sectional mean at the midpoint,
        # so a missing-factor stock gets a neutral ~0 z-score.
        col = col.fillna(col.median())
        z = _zscore(col)
        direction = directions.get(code, 1)
        if direction == -1:
            z = -z
        parts.append(z * weights[code])

    score = sum(parts)  # element-wise sum across factors
    # Normalise by total weight so scores stay comparable across configs.
    total_w = sum(abs(weights[c]) for c in usable_cols)
    if total_w == 0:
        return pd.Series(dtype=float)
    score = score / total_w
    return score.sort_values(ascending=False)


def select_top_n(
    score: pd.Series,
    factor_values: pd.DataFrame,
    n: int = 10,
    min_score: float | None = None,
) -> list[dict[str, Any]]:
    """Turn a composite score Series into a ranked top-N portfolio list.

    :param score: output of :func:`composite_score`, indexed by stock.
    :param factor_values: the raw per-factor DataFrame (for the breakdown).
    :param n: portfolio size.
    :param min_score: optional floor — drop stocks scoring below this even if
        they're in the top-N (avoids holding anti-signal junk in a bad market).
    :return: list of ``{stock, score, rank, factors: {code: value}}``,
        best first.
    """
    if score.empty or n <= 0:
        return []
    # Sort defensively: callers may pass an unsorted Series (e.g. when using
    # select_top_n standalone without composite_score). composite_score already
    # sorts, but this function must not rely on that contract.
    top = score.sort_values(ascending=False).head(n)
    if min_score is not None:
        top = top[top >= min_score]
    out: list[dict[str, Any]] = []
    for rank, (stock, sc) in enumerate(top.items(), start=1):
        row = factor_values.loc[stock] if stock in factor_values.index else pd.Series()
        out.append(
            {
                "stock": stock,
                "score": round(float(sc), 6),
                "rank": rank,
                "factors": {k: (None if pd.isna(v) else float(v)) for k, v in row.items()},
            }
        )
    return out


# ---------------------------------------------------------------------------
# DB-backed entry point: pulls factor values via the Factor registry, then
# delegates to the pure functions above.
# ---------------------------------------------------------------------------


def _collect_factor_values(
    db: Session,
    stocks: list[str],
    factor_codes: list[str],
    trade_date: date,
) -> pd.DataFrame:
    """Compute raw factor values for every (stock, factor) on ``trade_date``.

    Returns a wide DataFrame (index=stock, columns=factor_code). Missing
    factors (unknown code or compute() returned None) become NaN columns /
    cells so downstream imputation handles them uniformly.
    """
    rows: dict[str, dict[str, float | None]] = {s: {} for s in stocks}
    for code in factor_codes:
        factor = factor_registry.get(code)
        if factor is None:
            logger.warning("multi_factor: unknown factor code %r, skipping", code)
            continue
        for stock in stocks:
            try:
                v = factor.compute(db, stock, trade_date)
            except Exception:  # noqa: BLE001 — one bad stock shouldn't kill the run
                logger.exception("multi_factor: compute %s on %s failed", code, stock)
                v = None
            rows[stock][code] = v
    return pd.DataFrame.from_dict(rows, orient="index")


def score_stocks(
    db: Session,
    stocks: list[str],
    factor_specs: list[FactorWeight],
    trade_date: date,
    top_n: int = 10,
    min_score: float | None = None,
) -> list[dict[str, Any]]:
    """End-to-end multi-factor selection. The public entry point.

    Example::

        from app.factor.multi_factor import score_stocks, FactorWeight

        portfolio = score_stocks(
            db,
            stocks=["000001", "000002", ...],
            factor_specs=[
                FactorWeight("roc12", weight=1.0, direction=+1),   # momentum
                FactorWeight("rsi14", weight=0.5, direction=-1),   # not overbought
                FactorWeight("hv20",  weight=0.5, direction=-1),   # low volatility
            ],
            trade_date=date(2026, 7, 31),
            top_n=20,
        )

    :return: see :func:`select_top_n`.
    """
    if not stocks or not factor_specs:
        return []

    weights = {fw.code: fw.weight for fw in factor_specs}
    directions = {fw.code: fw.direction for fw in factor_specs}
    codes = list(weights.keys())

    raw = _collect_factor_values(db, stocks, codes, trade_date)
    if raw.empty:
        return []

    score = composite_score(raw, weights, directions)
    return select_top_n(score, raw, n=top_n, min_score=min_score)


# --- research-validated presets (V2.2 / BP-V2.2-001) -----------------------
#
# From the 2026-08 full-market RankIC survey (backend/reports/ic_survey_20260822.md):
# A-shares 2021-2026 are a reversal regime — momentum / liquidity / volatility
# names all carry significant negative IC, so the composite enters every factor
# with direction=-1 (oversold + low-vol + low-liquidity portfolio).
PRESET_V2_REVERSAL: list[FactorWeight] = [
    FactorWeight("amt20", 0.30, direction=-1),
    FactorWeight("roc120", 0.25, direction=-1),
    FactorWeight("hv20", 0.20, direction=-1),
    FactorWeight("rsi14", 0.15, direction=-1),
    FactorWeight("skew20", 0.10, direction=-1),
]

PRESETS: dict[str, list[FactorWeight]] = {
    "v2_reversal": PRESET_V2_REVERSAL,
}


def preset_meta() -> list[dict]:
    """Preset list for API/UI display."""
    return [
        {
            "name": name,
            "title": "V2 反转组合" if name == "v2_reversal" else name,
            "factors": [
                {"code": fw.code, "weight": fw.weight, "direction": fw.direction}
                for fw in specs
            ],
        }
        for name, specs in PRESETS.items()
    ]


def resolve_preset(name: str) -> list[FactorWeight] | None:
    """Factor specs for a preset name, or None if unknown."""
    return PRESETS.get(name)


__all__ = [
    "FactorWeight",
    "PRESETS",
    "PRESET_V2_REVERSAL",
    "composite_score",
    "preset_meta",
    "resolve_preset",
    "select_top_n",
    "score_stocks",
]
