"""Meta-labeling orchestration: DB → signals → labels → features → RF → compare.

Wires the pure :mod:`app.ml` pipeline to the database. Mirrors
``factor_service`` → ``app.factor``: this module only handles data loading,
bounds checking and result shaping; all quant logic stays in ``app.ml``.
"""

from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.ml import backtest as bt
from app.ml import barriers, evaluation, features, model, trendline
from app.models.stock import DailyPrice

logger = logging.getLogger(__name__)

# Indicators need ~40 bars of warm-up and the trendline needs two confirmed
# pivot lows before any signal — shorter histories can't produce valid samples.
MIN_BARS = 120
# The run executes synchronously inside the request; cap the universe so the
# response stays bounded (per-stock pipeline ≈ tens of ms).
MAX_STOCKS_PER_RUN = 50


def load_daily_df(
    db: Session, stock_code: str, start: date | None, end: date | None
) -> pd.DataFrame | None:
    """One stock's daily bars as a float frame (``trade_date`` is datetime64).

    ``None`` when the stock has fewer than :data:`MIN_BARS` valid closes — the
    caller skips it rather than failing the whole run.
    """
    q = select(DailyPrice).where(DailyPrice.stock_code == stock_code)
    if start is not None:
        q = q.where(DailyPrice.trade_date >= start)
    if end is not None:
        q = q.where(DailyPrice.trade_date <= end)
    rows = db.execute(q.order_by(DailyPrice.trade_date.asc())).scalars().all()
    if len(rows) < MIN_BARS:
        return None

    def _col(name: str) -> list[float]:
        return [float(getattr(r, name)) if getattr(r, name) is not None else np.nan
                for r in rows]

    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime([r.trade_date for r in rows]),
            "open": _col("open"),
            "high": _col("high"),
            "low": _col("low"),
            "close": _col("close"),
            "volume": _col("volume"),
        }
    )


def market_series(dfs: dict[str, pd.DataFrame]) -> pd.Series:
    """Equal-weight market index level from the loaded frames (date-indexed).

    Daily market return = median of the per-stock daily returns; the level is
    its cumulative product. Self-contained (no index table needed — the
    ``sa_index_quote`` history only reaches ~800 bars back). Used to derive
    the ``idx_ret20`` regime feature: trendline breakouts behave very
    differently in bull vs bear phases, and the referee needs to see which.
    """
    if not dfs:
        return pd.Series(dtype=float)
    closes = pd.concat(
        [df.set_index("trade_date")["close"].astype(float) for df in dfs.values()],
        axis=1,
    )
    med_ret = closes.pct_change().median(axis=1)
    return (1.0 + med_ret.fillna(0.0)).cumprod()


def _ret20_of(level: pd.Series) -> pd.Series:
    """20-trading-day return of a date-indexed level series."""
    return level / level.shift(20) - 1.0


def build_signal_frame(
    dfs: dict[str, pd.DataFrame],
    pt: float = 0.04,
    sl: float = 0.02,
    horizon: int = 10,
    order: int = 3,
    atr_barriers: bool = False,
) -> pd.DataFrame:
    """Signals → barrier labels + features, one row per labelled sample.

    Pure function over pre-loaded frames (unit-testable without a DB). Rows
    with NaN/inf features (signals too early in history) are dropped — the
    forest can't fit on them. With ``atr_barriers`` the barrier widths scale
    by the signal-day ATR ratio (see :func:`app.ml.barriers.triple_barrier`).
    """
    rows: list[dict] = []
    mkt_ret20 = _ret20_of(market_series(dfs))
    for code, df in dfs.items():
        sigs = trendline.trendline_signals(df, order=order)
        if sigs.empty:
            continue
        ind = features.indicator_frame(df)
        dates = df["trade_date"]
        close = df["close"].astype(float)
        for _, sig in sigs.iterrows():
            atr_ratio = None
            if atr_barriers:
                atr_ratio = float(ind["atr14"].iloc[int(sig["t"])]) / float(
                    close.iloc[int(sig["t"])]
                )
            lab = barriers.triple_barrier(
                df, int(sig["t"]), pt=pt, sl=sl, horizon=horizon,
                atr_pct=atr_ratio,
            )
            if lab is None:
                continue
            rows.append(
                {
                    "stock_code": code,
                    "trade_date": sig["trade_date"],
                    "t_end_date": dates.iloc[lab["t_end"]],
                    "label": lab["label"],
                    "ret": lab["ret"],
                    "hold": lab["hold"],
                    **features.build_features(df, ind, sig, mkt_ret20=mkt_ret20),
                }
            )
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).replace([np.inf, -np.inf], np.nan)
    return frame.dropna(subset=features.FEATS).reset_index(drop=True)


def run_meta_label(
    db: Session,
    stock_codes: list[str],
    start: date | None = None,
    end: date | None = None,
    *,
    pt: float = 0.04,
    sl: float = 0.02,
    horizon: int = 10,
    prob_th: float = 0.5,
    order: int = 3,
    init_train: int = 200,
    step: int = 50,
    embargo_days: int = 14,
    atr_barriers: bool = False,
) -> dict:
    """Full meta-labeling run → raw vs ML-filtered performance comparison.

    Raises :class:`BizError` when the inputs can't support a run (no data /
    too few signals), so the API renders a readable message instead of a
    half-empty payload.
    """
    if not stock_codes:
        raise BizError(400, "stock_codes 不能为空")

    dfs: dict[str, pd.DataFrame] = {}
    for code in stock_codes[:MAX_STOCKS_PER_RUN]:
        df = load_daily_df(db, code, start, end)
        if df is None:
            logger.info("meta-label: skip %s (fewer than %d bars)", code, MIN_BARS)
            continue
        dfs[code] = df
    if not dfs:
        raise BizError(400, f"没有股票满足最少 {MIN_BARS} 根日K的数据要求")

    frame = build_signal_frame(
        dfs, pt=pt, sl=sl, horizon=horizon, order=order, atr_barriers=atr_barriers
    )
    if len(frame) <= init_train:
        raise BizError(
            400,
            f"标注样本不足（{len(frame)} 条 ≤ init_train={init_train}），"
            "请扩大时间范围、增加股票数量或调小 init_train",
        )

    pred, clf, folds = model.walk_forward(
        frame, init_train=init_train, step=step, embargo_days=embargo_days,
        return_folds=True,
    )
    if pred.empty:
        raise BizError(400, "walk-forward 没有产出任何有效折（可尝试调小 init_train / embargo_days）")

    taken = pred[pred["prob"] > prob_th]
    return {
        "params": {
            "pt": pt, "sl": sl, "horizon": horizon, "prob_th": prob_th,
            "order": order, "init_train": init_train, "step": step,
            "embargo_days": embargo_days, "cost": barriers.COST,
            "atr_barriers": atr_barriers,
        },
        "stocks_used": list(dfs.keys()),
        "n_signals": int(len(frame)),
        "n_predicted": int(len(pred)),
        "raw": bt.perf(pred),
        "filtered": bt.perf(taken),
        "equity": {
            "raw": bt.equity_curve(pred),
            "filtered": bt.equity_curve(taken),
        },
        "feature_importance": [
            {"feature": f, "importance": round(float(v), 4)}
            for f, v in sorted(
                zip(features.FEATS, clf.feature_importances_), key=lambda x: -x[1]
            )
        ]
        if clf is not None
        else [],
        # V2.2 T2.6: auditable OOS evaluation — overall / by-year classification,
        # threshold sweep (trading + precision view), probability calibration,
        # and importance stability across folds.
        "evaluation": {
            "overall": evaluation.classification_metrics(pred["label"], pred["prob"]),
            "by_year": evaluation.by_year(pred),
            "threshold_sweep": evaluation.threshold_sweep(pred),
            "calibration": evaluation.calibration_bins(pred),
            "feature_importance_stability": evaluation.importance_stability(
                [f["importances"] for f in folds]
            ),
        },
    }
