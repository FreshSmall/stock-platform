"""全市场 Meta-Labeling 评估报告（V2.2 T2.6，离线研究脚本，不经 API 上限）.

用法（backend/ 目录下）:
    .venv/bin/python scripts/run_meta_label_full.py            # 全流程
    .venv/bin/python scripts/run_meta_label_full.py --reuse    # 复用缓存的信号帧
    .venv/bin/python scripts/run_meta_label_full.py --atr      # ATR 自适应屏障

流程：全市场深历史股票 → 趋势线突破信号 → 三重屏障标注 → 特征 →
purged walk-forward 随机森林 → 完整 OOS 评估（总体/分年 Precision-Recall-AUC、
阈值扫描、概率校准、特征重要性稳定性）→ markdown + JSON 报告落
``reports/meta_label_eval_<date>.md`` / ``.json``（JSON 供研究报告生成器引用）。

信号帧缓存为 pickle（--reuse 直接读），重调模型参数时无需重新扫库。
"""

from __future__ import annotations

import argparse
import json
import logging
import pickle
import time
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.models.stock import DailyPrice
from app.ml import backtest as bt
from app.ml import barriers, features, model
from app.services import meta_label_service as mls

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("meta-label-full")

CACHE = Path(__file__).resolve().parent.parent / ".cache_meta_label_frame.pkl"
REPORTS = Path(__file__).resolve().parent.parent / "reports"
REPORTS.mkdir(exist_ok=True)


def write_report(md_path, json_path, cfg, frame, pred, overall, by_year,
                 sweep, calib, imp_stab, raw, folds) -> None:
    """Markdown（人读）+ JSON（研究报告生成器引用）双输出。"""
    def _v(x, pct=False):
        if x is None:
            return "--"
        return f"{x:.1%}" if pct else f"{x:.4f}"

    lines = [
        "# Meta-Labeling 全市场 OOS 评估报告",
        "",
        f"- 生成日期：{date.today().isoformat()}（变体：**{cfg['variant']}** 屏障）",
        f"- 参数：pt={cfg['pt']} / sl={cfg['sl']} / horizon={cfg['horizon']}"
        f" / init_train={cfg['init_train']} / step={cfg['step']}"
        f" / 单笔往返成本={cfg['cost']:.2%}",
        f"- 样本：标注信号 {len(frame):,} 条，OOS 预测 {len(pred):,} 条"
        f"（信号帧{'复用缓存' if cfg['cache_reused'] else '现场重建'}）",
        "",
        "## 一、总体分类指标（阈值 0.5）",
        "",
        "| n | Precision | Recall | F1 | Accuracy | AUC |",
        "| --- | --- | --- | --- | --- | --- |",
        f"| {overall['n']:,} | {_v(overall['precision'])} | {_v(overall['recall'])}"
        f" | {_v(overall['f1'])} | {_v(overall['accuracy'])} | {_v(overall['auc'])} |",
        "",
        "## 二、分年 Precision / Recall / AUC",
        "",
        "| 年份 | n | Precision | Recall | F1 | Accuracy | AUC |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for yr, m in sorted(by_year.items()):
        lines.append(
            f"| {yr} | {m['n']:,} | {_v(m['precision'])} | {_v(m['recall'])}"
            f" | {_v(m['f1'])} | {_v(m['accuracy'])} | {_v(m['auc'])} |"
        )
    lines += [
        "",
        "## 三、阈值扫描（分类视角 × 交易视角，成本已含在每笔收益中）",
        "",
        "| 阈值 | 采用信号数 | 覆盖率 | Precision | 胜率 | PF | 单笔均值 | 年化(日组合) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in sweep:
        lines.append(
            f"| {r['threshold']:.2f} | {r['n_taken']:,} | {_v(r['coverage'], pct=True)}"
            f" | {_v(r['precision'])} | {_v(r.get('win_rate'), pct=True)}"
            f" | {r.get('profit_factor') if r.get('profit_factor') is not None else '--'}"
            f" | {_v(r.get('avg_ret'), pct=True)}"
            f" | {_v(r.get('ann_ret_dailycap'), pct=True)} |"
        )
    lines.append("")
    lines.append(
        "> 年化(日组合)口径：按信号进入日聚合，同日多笔取等权均值后按日复利——"
        "隐含\"同日信号等权分仓、不同日滚动复投\"的可解释资金曲线。"
        "逐笔全仓复利口径（total_ret）在数万条并发信号下会产出无意义的天文数字，故不再展示。"
    )
    lines += [
        "",
        "## 四、概率校准（预测概率 vs 实际胜率）",
        "",
        "| 分位桶 | 概率区间 | n | 平均预测 | 实际正例率 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for c in calib:
        lo, hi = c["prob_range"]
        lines.append(
            f"| {c['bin']} | [{lo:.2f}, {hi:.2f}] | {c['n']:,}"
            f" | {c['mean_prob']:.4f} | {c['actual_pos_rate']:.4f} |"
        )
    lines += [
        "",
        "## 五、特征重要性（跨折稳定性，按均值排序）",
        "",
        "| 特征 | 均值 | 标准差 | CV（越小越稳定） |",
        "| --- | --- | --- | --- |",
    ]
    for r in imp_stab:
        lines.append(
            f"| {r['feature']} | {r['mean']:.4f} | {r['std']:.4f}"
            f" | {r['cv'] if r['cv'] is not None else '--'} |"
        )
    lines += [
        "",
        "## 六、原始 vs 过滤绩效基准",
        "",
        f"原始（全信号）：{raw['n_trades']:,} 笔，胜率 {_v(raw.get('win_rate'), pct=True)}，"
        f"单笔均值 {_v(raw.get('avg_ret'), pct=True)}，"
        f"年化(日组合) {_v(raw.get('ann_ret_dailycap'), pct=True)}。",
        "",
        "## 局限性",
        "",
        "- 阈值在全样本 OOS 上扫描展示，正式选用应结合分年表现并考虑样本外再验证；",
        "- 交易视角为『信号流』口径：每笔独立、同日并发等权分仓，与组合级资金管理不同；",
        "- 成本为固定 0.15% 往返近似，未随流动性变化；止损按屏障价成交（跳空穿透偏乐观）；",
        "- 样本未过滤 ST/停牌（仅以开盘跳空 >9.5% 丢弃近似）；",
        f"- 共 {len(folds)} 个 expanding 折，早期折训练样本较少，指标波动大。",
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")

    summary = {
        "generated": date.today().isoformat(),
        "config": cfg,
        "overall": overall,
        "by_year": by_year,
        "threshold_sweep": sweep,
        "calibration": calib,
        "importance_stability": imp_stab,
        "raw_perf": raw,
        "n_folds": len(folds),
    }
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

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
        missing = [f for f in features.FEATS if f not in frame.columns]
        if missing:
            raise SystemExit(
                f"缓存信号帧缺少特征列 {missing}（旧版特征集）。"
                "请去掉 --reuse 重建缓存，或改用含完整特征的缓存变体。"
            )
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

    pred, clf, folds = model.walk_forward(
        frame, init_train=INIT_TRAIN, step=STEP, return_folds=True
    )
    log.info("walk-forward done in %.0fs (folds=%d)", time.time() - t0, len(folds))

    # --- V2.2 T2.6: full evaluation + report ---
    from app.ml import evaluation as mle

    overall = mle.classification_metrics(pred["label"], pred["prob"])
    by_year = mle.by_year(pred)
    sweep = mle.threshold_sweep(pred)
    calib = mle.calibration_bins(pred)
    imp_stab = mle.importance_stability([f["importances"] for f in folds])
    raw = bt.perf(pred)

    tag = date.today().strftime("%Y%m%d")
    variant = "atr" if args.atr else "fixed"
    report_path = REPORTS / f"meta_label_eval_{tag}_{variant}.md"
    json_path = REPORTS / f"meta_label_eval_{tag}_{variant}.json"
    write_report(report_path, json_path, {
        "variant": variant, "pt": PT, "sl": SL, "horizon": HORIZON,
        "init_train": INIT_TRAIN, "step": STEP, "cost": barriers.COST,
        "cache_reused": bool(args.reuse and cache.exists()),
    }, frame, pred, overall, by_year, sweep, calib, imp_stab, raw, folds)
    print(f"report written: {report_path}")

    fi = sorted(zip(features.FEATS, clf.feature_importances_), key=lambda x: -x[1])
    print("\nfeature importance:", [(f, round(float(v), 3)) for f, v in fi])
    print(f"\n--- total {time.time() - t0:.0f}s ---")


if __name__ == "__main__":
    main()
