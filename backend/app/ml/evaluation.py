"""OOS evaluation for the meta-label referee (V2.2 T2.6 / BP-V2.2-006).

All functions operate on the walk-forward OOS prediction frame — columns
``trade_date`` / ``label`` / ``prob`` / ``ret`` — and are pure (no DB, no
model), so the classification / threshold-calibration / yearly numbers are
identical between the API path and the full-market research script, and
unit-testable on synthetic frames.

Why this layer exists: before V2.2 the only OOS readout was accuracy@0.5 plus
a hand-picked threshold's P&L — thresholds chosen on the SAME predictions
they are evaluated on (selective overfitting). The sweep / calibration /
by-year tables make that choice auditable instead of anecdotal.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from app.ml import backtest as bt

# Default sweep grid: around the historical 0.5-0.6 sweet spot, wide enough
# to show the precision/coverage trade-off curve.
DEFAULT_THRESHOLDS = (0.45, 0.5, 0.55, 0.6, 0.65, 0.7)


def classification_metrics(
    y_true: pd.Series | np.ndarray, prob: pd.Series | np.ndarray, th: float = 0.5
) -> dict:
    """Precision / recall / F1 / accuracy / AUC at one threshold.

    AUC uses the raw probabilities (threshold-free). Metrics that are
    undefined (no positives predicted, single-class truth) come back None —
    the caller renders "--" rather than a fake 0.
    """
    y = np.asarray(y_true, dtype=int)
    p = np.asarray(prob, dtype=float)
    if len(y) == 0:
        return {"n": 0, "precision": None, "recall": None, "f1": None,
                "accuracy": None, "auc": None}
    y_pred = (p > th).astype(int)
    tp = int(((y_pred == 1) & (y == 1)).sum())
    fp = int(((y_pred == 1) & (y == 0)).sum())
    fn = int(((y_pred == 0) & (y == 1)).sum())
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision and recall
        else None
    )
    auc = None
    if 0 < y.sum() < len(y):  # both classes present
        auc = float(roc_auc_score(y, p))
    return {
        "n": int(len(y)),
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "accuracy": round(float((y_pred == y).mean()), 4),
        "auc": round(auc, 4) if auc is not None else None,
    }


def by_year(pred: pd.DataFrame, th: float = 0.5) -> dict[str, dict]:
    """Classification metrics per calendar year of the OOS signals."""
    if pred is None or pred.empty:
        return {}
    years = pd.to_datetime(pred["trade_date"]).dt.year
    out: dict[str, dict] = {}
    for yr, grp in pred.groupby(years):
        out[str(yr)] = classification_metrics(grp["label"], grp["prob"], th)
    return out


def threshold_sweep(
    pred: pd.DataFrame, thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS
) -> list[dict]:
    """Per-threshold view: trading P&L AND classification precision.

    ``coverage`` = share of OOS signals taken; ``precision`` here means
    P(label=1 | taken) — the referee's actual hit-rate on trades it allows.
    P&L via :func:`app.ml.backtest.perf` (cost already inside each ``ret``).
    """
    if pred is None or pred.empty:
        return []
    out: list[dict] = []
    n_total = len(pred)
    for th in thresholds:
        taken = pred[pred["prob"] > th]
        row: dict = {"threshold": th, "n_taken": int(len(taken))}
        row["coverage"] = round(len(taken) / n_total, 4) if n_total else None
        row["precision"] = (
            round(float(taken["label"].mean()), 4) if len(taken) else None
        )
        row.update(bt.perf(taken))
        out.append(row)
    return out


def calibration_bins(pred: pd.DataFrame, n_bins: int = 5) -> list[dict]:
    """Reliability check: does P(label=1) actually mean what it says?

    Signals are bucketed by predicted probability; each bucket reports the
    empirical positive rate. A well-calibrated referee has actual ≈ predicted
    monotonically across buckets.
    """
    if pred is None or pred.empty:
        return []
    p = pred["prob"].astype(float)
    try:
        bins = pd.qcut(p, n_bins, labels=False, duplicates="drop")
    except ValueError:
        return []
    out: list[dict] = []
    for b in sorted(bins.dropna().unique()):
        mask = bins == b
        grp = pred[mask]
        lo, hi = float(grp["prob"].min()), float(grp["prob"].max())
        out.append(
            {
                "bin": int(b) + 1,
                "prob_range": [round(lo, 3), round(hi, 3)],
                "n": int(len(grp)),
                "mean_prob": round(float(grp["prob"].mean()), 4),
                "actual_pos_rate": round(float(grp["label"].mean()), 4),
            }
        )
    return out


def importance_stability(
    fold_importances: list[dict[str, float]],
) -> list[dict]:
    """Feature importance across folds → mean / std / coefficient of variation.

    Input: one ``{feature: importance}`` dict per walk-forward fold. A feature
    whose importance swings wildly across folds is being opportunistically
    used — worth watching before trusting it.
    """
    if not fold_importances:
        return []
    frame = pd.DataFrame(fold_importances)
    out = []
    for feat in frame.columns:
        series = frame[feat].astype(float)
        mean = float(series.mean())
        std = float(series.std(ddof=1)) if len(series) > 1 else 0.0
        out.append(
            {
                "feature": feat,
                "mean": round(mean, 4),
                "std": round(std, 4),
                "cv": round(std / mean, 3) if mean > 0 else None,
                "n_folds": int(len(series)),
            }
        )
    out.sort(key=lambda r: -r["mean"])
    return out
