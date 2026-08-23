"""Random-forest meta-model with purged walk-forward training.

Signals are ordered by signal date and split into an expanding training window
and rolling test folds — never shuffled. Before each fit, training samples
whose barrier window (``trade_date`` .. ``t_end_date``) reaches within
``embargo_days`` calendar days of the test period are purged: without this, a
training trade's label could encode price action inside the test period
(lookahead via overlapping barrier windows). Folds whose purged training set
loses a class are skipped — their test signals get no prediction and are
excluded from the comparison rather than guessed.
"""

import pandas as pd
from sklearn.ensemble import RandomForestClassifier

from app.ml.features import FEATS

# Wide-ish forest with heavy leaf regularisation: financial features have a
# tiny signal-to-noise ratio, so deep trees just memorise noise.
RF_PARAMS = dict(
    n_estimators=400,
    min_samples_leaf=20,
    max_features="sqrt",
    class_weight="balanced_subsample",
    random_state=42,
    n_jobs=-1,
)


def purge(
    train: pd.DataFrame, test_start: pd.Timestamp, embargo_days: int
) -> pd.DataFrame:
    """Drop training rows whose barrier window invades the test period.

    A sample survives only if its barrier fully ends before
    ``test_start - embargo_days``.
    """
    cutoff = pd.Timestamp(test_start) - pd.Timedelta(days=embargo_days)
    return train[pd.to_datetime(train["t_end_date"]) < cutoff]


def walk_forward(
    sigs: pd.DataFrame,
    init_train: int = 200,
    step: int = 50,
    embargo_days: int = 14,
    rf_overrides: dict | None = None,
) -> tuple[pd.DataFrame, RandomForestClassifier | None]:
    """Score every signal out-of-sample with an expanding-window forest.

    Expects columns ``trade_date`` / ``t_end_date`` / ``label`` plus
    :data:`FEATS`; rows with NaN features must be dropped by the caller.
    Returns the concatenated test folds with a ``prob`` column (P(label=1)),
    and the last fitted model (for feature importances) or ``None``.
    """
    params = {**RF_PARAMS, **(rf_overrides or {})}
    sigs = sigs.sort_values("trade_date").reset_index(drop=True)

    preds: list[pd.DataFrame] = []
    last_model: RandomForestClassifier | None = None
    for k in range(init_train, len(sigs), step):
        test = sigs.iloc[k : k + step]
        train = purge(sigs.iloc[:k], test["trade_date"].min(), embargo_days)
        if train["label"].nunique() < 2:
            continue
        clf = RandomForestClassifier(**params)
        clf.fit(train[FEATS], train["label"])
        prob_col = list(clf.classes_).index(1)
        out = test.copy()
        out["prob"] = clf.predict_proba(test[FEATS])[:, prob_col]
        preds.append(out)
        last_model = clf

    if not preds:
        return pd.DataFrame(), None
    return pd.concat(preds, ignore_index=True), last_model
