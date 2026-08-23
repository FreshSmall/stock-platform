"""Meta-labeling pipeline: rule signals + ML referee (AFML ch.3/ch.7 style).

The primary model (:mod:`app.ml.trendline`) generates ascending-trendline
breakout signals with no lookahead. Each signal is labelled true/false by the
triple-barrier method (:mod:`app.ml.barriers`), featurised strictly from data
at or before the signal day (:mod:`app.ml.features`), and scored by a
random forest trained with purged walk-forward folds (:mod:`app.ml.model`).
Only signals whose predicted win probability clears a threshold are traded —
performance with vs without that filter is compared in :mod:`app.ml.backtest`.

All modules here are pure (pandas/numpy/sklearn, no DB); DB wiring lives in
:mod:`app.services.meta_label_service`.
"""
