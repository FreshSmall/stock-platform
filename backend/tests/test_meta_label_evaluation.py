"""Tests for the meta-label OOS evaluation layer (V2.2 T2.6 / BP-V2.2-006).

Pure-function tests on synthetic prediction frames — the numbers must be
exactly derivable by hand so the API path and the full-market script (which
share these functions) can't silently disagree.
"""

import numpy as np
import pandas as pd

from app.ml import evaluation as mle
from app.ml import model


def _pred_frame():
    """8 signals with hand-computable metrics.

    taken = prob>0.5 → rows 0-3, preds [1,1,1,1], truth [1,1,0,1]
    → TP=3, FP=1, FN=2 (rows 5,7) → precision 3/4, recall 3/5, f1 2/3.
    """
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(
                ["2025-01-05", "2025-01-20", "2025-03-01", "2025-03-15",
                 "2025-05-05", "2025-05-20", "2026-01-05", "2026-01-20"]
            ),
            "label": [1, 1, 0, 1, 0, 1, 0, 1],
            "prob": [0.9, 0.8, 0.7, 0.6, 0.4, 0.45, 0.3, 0.2],
            "ret": [0.05, -0.02, 0.06, -0.02, -0.02, 0.05, -0.02, 0.05],
            "hold": [5, 6, 4, 8, 5, 6, 7, 5],
        }
    )


def test_classification_metrics_hand_computed():
    m = mle.classification_metrics(
        [1, 1, 0, 1, 0, 1, 0, 1], [0.9, 0.8, 0.7, 0.6, 0.4, 0.45, 0.3, 0.2]
    )
    assert m["n"] == 8
    assert m["precision"] == 0.75
    assert m["recall"] == 0.6
    assert m["f1"] == round(2 / 3, 4)
    # AUC: verify it's computed and sane
    assert m["auc"] is not None and 0.0 <= m["auc"] <= 1.0


def test_classification_metrics_single_class_returns_none_auc():
    m = mle.classification_metrics([1, 1, 1], [0.9, 0.8, 0.7])
    assert m["auc"] is None


def test_by_year_groups_and_shapes():
    out = mle.by_year(_pred_frame())
    assert set(out) == {"2025", "2026"}
    assert out["2025"]["n"] == 6
    assert out["2026"]["n"] == 2


def test_threshold_sweep_monotonic_coverage_and_precision():
    sweep = mle.threshold_sweep(_pred_frame(), thresholds=(0.3, 0.5, 0.7))
    # coverage shrinks as the threshold rises
    cov = [r["coverage"] for r in sweep]
    assert cov == sorted(cov, reverse=True)
    # n_taken matches the prob filter exactly
    frame = _pred_frame()
    for r in sweep:
        assert r["n_taken"] == int((frame["prob"] > r["threshold"]).sum())
    # precision = P(label=1 | taken)
    th = 0.5
    row = next(r for r in sweep if r["threshold"] == th)
    taken = frame[frame["prob"] > th]
    assert row["precision"] == round(float(taken["label"].mean()), 4)
    # trading view fields present (bt.perf merged)
    assert "win_rate" in row and "total_ret" in row


def test_calibration_buckets_empirical_rate():
    calib = mle.calibration_bins(_pred_frame(), n_bins=2)
    assert len(calib) == 2
    # the low-prob bucket's empirical rate must be below the high-prob one
    assert calib[0]["actual_pos_rate"] <= calib[-1]["actual_pos_rate"]


def test_importance_stability_cv():
    folds = [
        {"a": 0.5, "b": 0.3},
        {"a": 0.3, "b": 0.4},
        {"a": 0.4, "b": 0.3},
    ]
    out = mle.importance_stability(folds)
    by_feat = {r["feature"]: r for r in out}
    assert by_feat["a"]["mean"] == round(0.4, 4)
    assert by_feat["a"]["n_folds"] == 3
    # cv = std/mean, present and positive
    assert by_feat["a"]["cv"] is not None and by_feat["a"]["cv"] > 0


def test_walk_forward_return_folds_contract():
    """return_folds=True yields per-fold importances without changing preds."""
    rng = np.random.default_rng(0)
    n = 120
    frame = pd.DataFrame(
        {
            "trade_date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "t_end_date": pd.date_range("2024-01-11", periods=n, freq="D"),
            "label": rng.integers(0, 2, n),
            **{
                f: rng.normal(size=n) for f in (
                    "slope_norm", "dev", "adx14", "atr_pct", "vol_ratio", "rsi12",
                    "dist_ma20", "ret5", "ll_span", "bars_since", "log_amt20", "idx_ret20",
                )
            },
        }
    )
    pred2, clf2 = model.walk_forward(frame, init_train=50, step=20)
    pred3, clf3, folds = model.walk_forward(
        frame, init_train=50, step=20, return_folds=True
    )
    assert len(pred2) == len(pred3)
    assert clf3 is not None
    assert len(folds) >= 1
    first = folds[0]
    assert set(first) == {"test_start", "n_train", "importances"}
    assert set(first["importances"]) == set(model.FEATS)
