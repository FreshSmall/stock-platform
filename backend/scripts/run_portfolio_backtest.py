"""组合级回测：把 meta-label 信号流变成带仓位约束的净值曲线.

用法（backend/ 目录下）:
    .venv/bin/python scripts/run_portfolio_backtest.py          # 用缓存的 ATR 信号帧
    .venv/bin/python scripts/run_portfolio_backtest.py --slots 5

模拟规则（比"每笔全仓顺序复利"更接近可交易口径）:
- 信号按日期先后处理，同日按模型置信度（prob）高者优先占用空位；
- 最多同时持有 --slots 只，每只仓位 = 1/slots（闲置资金收益记 0）；
- 单笔收益用三重屏障出场的净收益（已扣 0.15% 双边成本），作用于其仓位份额;
- 净值按平仓事件时间累计，最大回撤在该事件曲线上计算。
输出：raw / 概率过滤 / 市场状态条件 等变体的年化、回撤、交易数、胜率，
以及 idx_ret20 分组的市场状态归因。
"""

from __future__ import annotations

import argparse
import heapq
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from app.ml import backtest as bt
from app.ml import model

CACHE = Path(__file__).resolve().parent.parent / ".cache_meta_label_frame.atr.pkl"
INIT_TRAIN, STEP = 5000, 2000


def portfolio_backtest(
    pred: pd.DataFrame, max_slots: int = 10, prob_th: float = 0.0
) -> dict:
    """带并发仓位上限的等权组合模拟。"""
    sig = pred[pred["prob"] > prob_th] if prob_th > 0 else pred
    sig = sig.sort_values(["trade_date", "prob"], ascending=[True, False])

    active_end: list = []  # min-heap of t_end_date
    frac = 1.0 / max_slots
    eq, n, wins = 1.0, 0, 0
    curve_d, curve_v = [], []
    for row in sig.itertuples():
        d = row.trade_date
        while active_end and active_end[0] <= d:
            heapq.heappop(active_end)
        if len(active_end) >= max_slots:
            continue
        heapq.heappush(active_end, row.t_end_date)
        eq *= 1.0 + row.ret * frac
        n += 1
        wins += row.ret > 0
        curve_d.append(row.t_end_date)
        curve_v.append(eq)

    if n == 0:
        return {"n": 0}
    curve = pd.Series(curve_v, index=pd.DatetimeIndex(curve_d))
    days = max((curve.index[-1] - sig["trade_date"].min()).days, 1)
    ann = eq ** (365.0 / days) - 1.0
    dd = float((curve / curve.cummax() - 1.0).min())
    rets = sig  # per-trade stats of *taken* subset unavailable here; use eq stats
    return {
        "n_trades": n,
        "win_rate": round(wins / n, 3),
        "total_ret": round(eq - 1.0, 3),
        "annualized": round(ann, 3),
        "max_drawdown": round(dd, 3),
        "calmar": round(ann / abs(dd), 2) if dd < 0 else None,
        "years": round(days / 365.0, 1),
    }


def regime_breakdown(pred: pd.DataFrame) -> pd.DataFrame:
    """市场状态（idx_ret20）分组归因。"""
    bins = [-np.inf, -0.05, 0.0, 0.05, np.inf]
    labels = ["深跌(<-5%)", "弱市(-5~0)", "温和(0~5%)", "强势(>5%)"]
    g = pred.assign(regime=pd.cut(pred["idx_ret20"], bins=bins, labels=labels))
    out = g.groupby("regime", observed=True).apply(
        lambda x: pd.Series(
            {
                "交易数": len(x),
                "胜率": round((x["ret"] > 0).mean(), 3),
                "单笔均值": round(x["ret"].mean(), 5),
                "PF": round(
                    x.loc[x.ret > 0, "ret"].sum()
                    / max(-x.loc[x.ret <= 0, "ret"].sum(), 1e-9),
                    2,
                ),
            }
        )
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slots", type=int, default=10)
    args = parser.parse_args()

    with open(CACHE, "rb") as f:
        frame = pickle.load(f)
    print(f"frame: {len(frame)} rows from cache")
    pred, _ = model.walk_forward(frame, init_train=INIT_TRAIN, step=STEP)
    print(f"out-of-sample predictions: {len(pred)}\n")

    variants: list[tuple[str, pd.DataFrame]] = [
        ("原始全信号", pred),
        ("过滤(P>0.50)", pred[pred["prob"] > 0.50]),
        ("过滤(P>0.55)", pred[pred["prob"] > 0.55]),
        ("仅牛市条件(idx>0)", pred[pred["idx_ret20"] > 0]),
        ("P>0.50 + idx>0", pred[(pred["prob"] > 0.50) & (pred["idx_ret20"] > 0)]),
    ]
    print(f"{'变体':<20}{'交易数':>7}{'胜率':>7}{'总收益':>9}{'年化':>8}{'最大回撤':>9}{'Calmar':>8}")
    for name, sub in variants:
        s = portfolio_backtest(sub, max_slots=args.slots)
        if s.get("n_trades", 0) == 0:
            print(f"{name:<20}{0:>7}")
            continue
        print(f"{name:<20}{s['n_trades']:>7}{s['win_rate']:>7.1%}{s['total_ret']:>9.1%}"
              f"{s['annualized']:>8.1%}{s['max_drawdown']:>9.1%}{s['calmar']!s:>8}")

    print(f"\n（组合约束：最多同时持有 {args.slots} 只，等权 1/{args.slots}，空仓资金零收益）")
    print("\n市场状态归因（全部信号，单笔口径）:")
    print(regime_breakdown(pred).to_string())


if __name__ == "__main__":
    main()
