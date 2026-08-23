"""全市场 Meta-Labeling 对比（离线研究脚本，不经 API 的 50 只上限）.

用法（backend/ 目录下）:
    .venv/bin/python scripts/run_meta_label_full.py            # 全流程
    .venv/bin/python scripts/run_meta_label_full.py --reuse    # 复用缓存的信号帧

流程：全市场深历史股票 → 趋势线突破信号 → 三重屏障标注 → 特征 →
purged walk-forward 随机森林 → 原始 vs 概率过滤的多阈值对比。

信号帧缓存为 pickle（--reuse 直接读），重调模型参数时无需重新扫库。
"""

from __future__ import annotations

import argparse
import logging
import os
import pickle
import time
from pathlib import Path

import numpy as np
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.models.stock import DailyPrice
from app.ml import backtest as bt
from app.ml import features, model
from app.services import meta_label_service as mls

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("meta-label-full")

CACHE = Path(__file__).resolve().parent.parent / ".cache_meta_label_frame.pkl"

# 全市场扫描参数（研究口径，比 API 默认更宽）
PT, SL, HORIZON = 0.04, 0.02, 10
INIT_TRAIN, STEP = 5000, 2000
MIN_BARS = 1000  # 只用补满深历史的股票


def load_universe(db) -> list[str]:
    n_sub = (
        select(DailyPrice.stock_code, func.count().label("n"))
        .group_by(DailyPrice.stock_code)
        .subquery()
    )
    codes = list(
        db.execute(
            select(n_sub.c.stock_code).where(n_sub.c.n > MIN_BARS)
        ).scalars()
    )
    return codes


def build_frame(codes: list[str], atr_barriers: bool):
    """全市场信号帧（分段开 session，避免长事务/大连接占用）。"""
    from app.core.database import SessionLocal

    dfs = {}
    t0 = time.time()
    for i, code in enumerate(codes):
        db = SessionLocal()
        try:
            df = mls.load_daily_df(db, code, None, None)
        finally:
            db.close()
        if df is not None:
            dfs[code] = df
        if (i + 1) % 250 == 0:
            log.info("loaded %d/%d stocks (%.0fs)", i + 1, len(codes), time.time() - t0)
    log.info("loaded %d stocks in %.0fs, building signal frame...", len(dfs), time.time() - t0)
    frame = mls.build_signal_frame(
        dfs, pt=PT, sl=SL, horizon=HORIZON, atr_barriers=atr_barriers
    )
    log.info("signal frame: %d rows (%.0fs)", len(frame), time.time() - t0)
    return frame


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse", action="store_true", help="复用缓存信号帧")
    parser.add_argument(
        "--atr", action="store_true",
        help="ATR 自适应屏障（2×ATR 上 / 1×ATR 下）+ 流动性/市场状态特征",
    )
    args = parser.parse_args()

    cache = CACHE.with_suffix(".atr.pkl") if args.atr else CACHE
    t0 = time.time()
    if args.reuse and cache.exists():
        with open(cache, "rb") as f:
            frame = pickle.load(f)
        log.info("reused cached frame: %d rows", len(frame))
    else:
        db = SessionLocal()
        try:
            codes = load_universe(db)
        finally:
            db.close()
        log.info("universe: %d stocks (> %d bars)", len(codes), MIN_BARS)
        frame = build_frame(codes, atr_barriers=args.atr)
        with open(cache, "wb") as f:
            pickle.dump(frame, f)
        log.info("frame cached to %s", cache)

    print(f"\nlabelled signals: {len(frame)} (label-1 ratio {frame['label'].mean():.2%})")

    pred, clf = model.walk_forward(frame, init_train=INIT_TRAIN, step=STEP)
    log.info("walk-forward done in %.0fs", time.time() - t0)
    print(f"out-of-sample predicted: {len(pred)}")
    acc = ((pred["prob"] > 0.5).astype(int) == pred["label"]).mean()
    p1 = pred.loc[pred["label"] == 1, "prob"]
    p0 = pred.loc[pred["label"] == 0, "prob"]
    print(f"OOS accuracy@0.5: {acc:.2%} | P̄(1)={p1.mean():.3f} vs P̄(0)={p0.mean():.3f}\n")

    header = f"{'策略':<14}{'交易数':>7}{'胜率':>8}{'PF':>7}{'单笔均值':>10}{'总收益':>9}{'最大回撤':>9}{'持仓':>6}"
    print(header)
    raw = bt.perf(pred)
    print(f"{'原始(全信号)':<14}{raw['n_trades']:>7}{raw['win_rate']:>8.1%}"
          f"{raw['profit_factor']!s:>7}{raw['avg_ret']:>10.4%}"
          f"{raw['total_ret']:>9.1%}{raw['max_drawdown']:>9.1%}{raw['avg_hold_days']:>6.1f}")
    for th in (0.5, 0.55, 0.6):
        s = bt.perf(pred[pred["prob"] > th])
        print(f"{f'过滤(P>{th})':<14}{s['n_trades']:>7}{s['win_rate']:>8.1%}"
              f"{s['profit_factor']!s:>7}{s['avg_ret']:>10.4%}"
              f"{s['total_ret']:>9.1%}{s['max_drawdown']:>9.1%}{s['avg_hold_days']:>6.1f}")

    fi = sorted(zip(features.FEATS, clf.feature_importances_), key=lambda x: -x[1])
    print("\nfeature importance:", [(f, round(float(v), 3)) for f, v in fi])
    print(f"\n--- total {time.time() - t0:.0f}s ---")


if __name__ == "__main__":
    main()
