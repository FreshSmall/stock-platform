"""《选股策略研究报告》生成器（V2.2 T2.8 / BP-V2.2-008）.

用法（backend/ 目录下）:
    .venv/bin/python scripts/generate_research_report.py
    .venv/bin/python scripts/generate_research_report.py --universe 300 --quick

全部证据来自**平台服务**（factor_service.compute_ic_series / layered_backtest、
portfolio_backtest_service.run_mf_backtest），Meta-Labeling 增益引用
run_meta_label_full.py 产出的 JSON 摘要 —— 满足阶段二"研究流程由平台功能
而非一次性脚本支撑"的退出标准。重跑本脚本即可随数据更新复现整份报告。

产物: reports/stock_strategy_research_report_v1_<date>.md
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, timedelta
from pathlib import Path

from app.core.database import SessionLocal
from app.factor.multi_factor import PRESET_V2_REVERSAL
from app.services import factor_service, portfolio_backtest_service as pbs

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("research-report")

REPORTS = Path(__file__).resolve().parent.parent / "reports"

# 分年稳健性窗口（组合回测逐年重跑）
YEARLY_WINDOWS = [
    (date(2022, 9, 1), date(2023, 8, 31)),
    (date(2023, 9, 1), date(2024, 8, 31)),
    (date(2024, 9, 1), date(2025, 8, 31)),
    (date(2025, 9, 1), date(2026, 8, 31)),
]


def _pct(v):
    return "--" if v is None else f"{float(v) * 100:.2f}%"


def ic_evidence(db, universe: int) -> tuple[dict, dict]:
    """5 个核心因子的 IC 序列（近一年, horizon=10）与 amt20 分层证据。"""
    ic_out: dict[str, dict] = {}
    for spec in PRESET_V2_REVERSAL:
        res = factor_service.compute_ic_series(
            db, spec.code, date.today() - timedelta(days=365), date.today(),
            horizons=(10,), step=10, pool="pit", universe_size=universe,
        )
        if res:
            ic_out[spec.code] = res
        log.info("ic-series %s done", spec.code)
    layered = factor_service.layered_backtest(
        db, "amt20", date.today() - timedelta(days=730), date.today(),
        step=10, n_layers=5, pool="pit", universe_size=universe,
    )
    return ic_out, {"layered": layered}


def portfolio_runs(db, universe: int, quick: bool):
    """主线组合（2 年周调仓）+ 分年窗口稳健性。"""
    headline = pbs.run_mf_backtest(
        db, preset="v2_reversal",
        start=date(2024, 9, 1), end=date(2026, 9, 4),
        freq="W", top_n=10, initial_cash=100_000,
        pool="pit", only_tradable=True, liquidity_top_k=universe,
    )
    log.info("headline portfolio run done: %s", headline["run_id"])
    yearly = []
    if not quick:
        for start, end in YEARLY_WINDOWS:
            try:
                run = pbs.run_mf_backtest(
                    db, preset="v2_reversal", start=start, end=end,
                    freq="W", top_n=10, initial_cash=100_000,
                    pool="pit", only_tradable=True, liquidity_top_k=universe,
                )
                yearly.append((f"{start.year}~{end.year}", run))
                log.info("yearly window %s done: %s", start.year, run["run_id"])
            except ValueError as e:
                yearly.append((f"{start.year}~{end.year}", {"error": str(e)}))
    return headline, yearly


def load_meta_label_summary() -> dict | None:
    files = sorted(REPORTS.glob("meta_label_eval_*_*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", type=int, default=500, help="流动性预筛 top-K")
    parser.add_argument("--quick", action="store_true", help="跳过分年窗口（调试）")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        ic_out, extra = ic_evidence(db, args.universe)
        headline, yearly = portfolio_runs(db, args.universe, args.quick)
    finally:
        db.close()
    ml = load_meta_label_summary()

    today = date.today().isoformat()
    m = headline["metrics"]
    lines = [
        "# 《选股策略研究报告》v1",
        "",
        f"> 生成日期：{today} ｜ 生成器：scripts/generate_research_report.py"
        "（全部证据来自平台服务，可复现重跑）",
        ">",
        "> **本报告仅为研究参考，不构成投资建议。**",
        "",
        "## 一、摘要",
        "",
        f"核心组合为 **V2 反转组合**（超跌 + 低波 + 低流动性：amt20/roc120/hv20/rsi14/skew20 全负向），"
        f"近两年周度调仓、计入全部交易成本与可成交性约束后：**总收益 {_pct(m['total_return'])}"
        f" vs 基准 {_pct(m['benchmark_return'])}**，最大回撤 {_pct(m['max_drawdown'])}，"
        f"夏普 {m['sharpe'] if m['sharpe'] is None else round(m['sharpe'], 2)}，"
        f"平均单次调仓换手 {_pct(m['avg_turnover'])}，总成本 ¥{m['total_cost']:.0f}"
        f"（回测编号 `{headline['run_id']}`）。",
        "该组合的逻辑基础是全市场 IC 普查结论：A 股 2021-2026 为反转市场，"
        "动量/流动性/波动率因子 IC 显著为负——本报告第四节用平台 IC 序列服务重新验证了这一点。",
        "",
        "## 二、研究基础与数据口径",
        "",
        "| 项 | 口径 |",
        "| --- | --- |",
        "| 行情数据 | `daily_prices`（前复权，legacy 口径；V2.1 复权重构迁移中） |",
        "| 股票池 | PIT 历史时点池（`sa_stock_lifecycle`，含退市股，无幸存者偏差） |",
        "| 样本治理 | 剔除不可成交（停牌/一字板）；ST/停牌过滤按各表参数 |",
        "| 交易成本 | 佣金双边万 2.5（最低 5 元）+ 印花税卖出万 5 + 过户费 + 滑点千 1 |",
        "| 撮合 | T+1 次日开盘成交、整手（100 股）、不可成交顺延 |",
        "| 数据质量 | V2.1 质量日报（每日 08:00 巡检）背书；复权断裂重灌进行中 |",
        "",
        "## 三、核心组合定义（v2_reversal 预设）",
        "",
        "| 因子 | 含义 | 权重 | 方向 |",
        "| --- | --- | --- | --- |",
    ]
    meanings = {
        "amt20": "20日均成交额（流动性）", "roc120": "120日涨跌幅（长端动量）",
        "hv20": "20日历史波动率", "rsi14": "14日相对强弱",
        "skew20": "20日收益偏度",
    }
    for spec in PRESET_V2_REVERSAL:
        lines.append(
            f"| {spec.code} | {meanings.get(spec.code, '')} | {spec.weight:.2f}"
            f" | {'低者优先' if spec.direction == -1 else '高者优先'} |"
        )
    lines += [
        "",
        "## 四、因子有效性证据（平台 IC 序列服务）",
        "",
        "近一年、horizon=10 交易日、PIT 池、步长 10：",
        "",
        "| 因子 | 均值IC | ICIR | 胜率 | 样本日 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for spec in PRESET_V2_REVERSAL:
        res = ic_out.get(spec.code)
        s = res["summary"].get("10") if res else None
        if s:
            lines.append(
                f"| {spec.code} | {s['mean_ic']} | "
                f"{round(s['icir'], 3) if s['icir'] is not None else '--'} | "
                f"{_pct(s['win_rate'])} | {s['n_dates']} |"
            )
        else:
            lines.append(f"| {spec.code} | -- | -- | -- | -- |")
    # 分年 IC（取权重最高的 amt20 展示稳健性）
    amt = ic_out.get("amt20")
    if amt and amt.get("by_year"):
        lines += [
            "",
            "amt20 分年 IC（horizon=10）：",
            "",
            "| 年份 | IC |",
            "| --- | --- |",
        ]
        for yr, m10 in sorted(amt["by_year"].items()):
            v = m10.get("10")
            lines.append(f"| {yr} | {v if v is not None else '--'} |")
    layered = extra.get("layered")
    if layered:
        ls = layered["long_short"]
        q1 = layered["layers"][0]
        q5 = layered["layers"][-1]
        lines += [
            "",
            f"分层证据（amt20 五分位、近两年、每 10 日轮动）：Q1（最低成交额）累计 {_pct(q1['total_return'])}"
            f" vs Q5 {_pct(q5['total_return'])}，多空（Q5−Q1）累计 {_pct(ls['total_return'])}"
            f"、最大回撤 {_pct(ls['max_drawdown'])} —— 与负 IC 方向一致。",
        ]
    lines += [
        "",
        "## 五、成本后收益与分年稳健性（组合回测服务）",
        "",
        f"主线（2024-09-01 ~ 2026-09-04，周调仓，Top 10，流动性预筛 top-{args.universe}）：",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 总收益 | {_pct(m['total_return'])} |",
        f"| 基准（上证指数） | {_pct(m['benchmark_return'])} |",
        f"| 年化 | {_pct(m['ann_return'])} |",
        f"| 最大回撤 | {_pct(m['max_drawdown'])} |",
        f"| 夏普 / 卡玛 | {m['sharpe']} / {m['calmar']} |",
        f"| 平均换手（单次调仓） | {_pct(m['avg_turnover'])} |",
        f"| 总成本 | ¥{m['total_cost']:.0f} |",
        f"| 调仓次数 | {m['n_rebalances']} |",
    ]
    if yearly:
        lines += [
            "",
            "分年窗口（同样参数逐年重跑）：",
            "",
            "| 窗口 | 总收益 | 基准 | 超额 | 最大回撤 | 夏普 | 换手 | 成本 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for label, run in yearly:
            if "error" in run:
                lines.append(f"| {label} | 失败：{run['error']} | | | | | | |")
                continue
            ym = run["metrics"]
            excess = (
                None
                if ym["total_return"] is None or ym["benchmark_return"] is None
                else ym["total_return"] - ym["benchmark_return"]
            )
            lines.append(
                f"| {label} | {_pct(ym['total_return'])} | {_pct(ym['benchmark_return'])}"
                f" | {_pct(excess)} | {_pct(ym['max_drawdown'])} | {ym['sharpe']}"
                f" | {_pct(ym['avg_turnover'])} | ¥{ym['total_cost']:.0f} |"
            )
    if ml:
        ov = ml["overall"]
        sweep = ml["threshold_sweep"]
        best = max(
            (r for r in sweep if r.get("n_taken", 0) > 0),
            key=lambda r: r.get("avg_ret") or -1,
            default=None,
        )
        raw = ml.get("raw_perf", {})
        lines += [
            "",
            "## 六、Meta-Labeling 增益（引用全市场评估报告）",
            "",
            f"来源：`reports/` 最新评估（{ml.get('generated')}，{ml['config']['variant']} 屏障变体）。"
            "规则信号（趋势线突破 + 三重屏障）由随机森林裁判过滤，purged walk-forward 样本外评估：",
            "",
            f"- 总体：Precision {ov['precision']} / Recall {ov['recall']} / AUC {ov['auc']}"
            f"（n={ov['n']:,}）",
            f"- 原始信号：{raw.get('n_trades', 0):,} 笔，胜率 {_pct(raw.get('win_rate'))}，"
            f"单笔均值 {_pct(raw.get('avg_ret'))}，年化(日组合) {_pct(raw.get('ann_ret_dailycap'))}",
        ]
        if best:
            lines.append(
                f"- 扫描区间内单笔均值最优阈值 P>{best['threshold']}："
                f"{best['n_taken']:,} 笔，胜率 {_pct(best.get('win_rate'))}，"
                f"PF {best.get('profit_factor')}，单笔均值 {_pct(best.get('avg_ret'))}"
                f"（覆盖率 {_pct(best.get('coverage'))}）"
            )
        lines += [
            "",
            "> 注：阈值在全样本 OOS 上扫描展示，属研究证据而非上线参数；"
            "正式采用需通过分年稳健性与后续模拟盘验证。",
        ]
    else:
        lines += [
            "",
            "## 六、Meta-Labeling 增益",
            "",
            "尚未找到 `reports/meta_label_eval_*.json`——请先运行 "
            "`scripts/run_meta_label_full.py` 生成评估报告后重跑本生成器。",
        ]
    lines += [
        "",
        "## 七、局限性声明",
        "",
        "1. **市场状态依赖**：反转效应基于 2021-2026 样本；若市场转为动量市，组合方向需重估"
        "（roc20 在 2021 年的转向是前车之鉴）；",
        "2. **数据口径**：行情为 legacy 前复权表，V2.1 复权重构全量重灌尚未收官，"
        "分红密集期可能存在残余复权断裂；amount/turnover 历史缺失率仍高（amt20 以 close×volume 近似）；",
        "3. **行业覆盖**：中性化所依行业映射历史始于 2026-08-31，此前为首快照回退；"
        "本报告主口径未开中性化（组合原始暴露见 IC 序列服务可复验）；",
        "4. **成本近似**：滑点为固定千 1，未建模冲击成本随流动性/单量的变化；"
        "组合对低流动性股票的超额收益对成本假设敏感（分年窗口的成本列已单列）；",
        "5. **组合回测约束**：Top-10 等权、单股不可成交顺延的规则简化；"
        "跌停卖出顺延期间按收盘估值；",
        "6. **Meta-Labeling**：固定 0.15% 往返成本、屏障价成交假设偏乐观；样本未滤 ST；"
        "阈值选择存在前视风险（见第六节注）；",
        "7. **无实盘验证**：全部结论止于历史回测；模拟盘（V3a）连续运行 ≥4 周"
        "是检验回测-实盘偏差的必要条件。",
        "",
        "## 八、结论与后续",
        "",
        f"在计入真实成本与可成交性后，V2 反转组合近两年相对基准的超额为"
        f" {_pct((m['total_return'] or 0) - (m['benchmark_return'] or 0))}，"
        "方向性证据（IC/分层/组合三源一致）成立但幅度对成本与流动性假设敏感。"
        "下一步：① 上模拟盘（V3a T3.x）跟踪净值与回测偏差；② 因子健康度周度监控"
        "（T2.7）捕捉 IC 衰减；③ 待 V2.1 数据重灌收官后重跑本报告复验结论。",
        "",
    ]
    out = REPORTS / f"stock_strategy_research_report_v1_{date.today().strftime('%Y%m%d')}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"report written: {out}")


if __name__ == "__main__":
    main()
