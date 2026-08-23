"""全市场因子 RankIC 普查（5 年 × 全 A，向量化实现）。

对 ``daily_prices`` 里全部股票构造 date × stock 面板，向量化计算量价类
因子（公式与 ``app/factor`` 注册因子一致；价格水平类因子改用跨股票可比
的归一化形式），再按固定间隔取调仓日，计算每个调仓日横截面 Spearman
RankIC（因子值 vs 未来 ``horizon`` 日收益），汇总：

* mean IC / ICIR / t-stat / IC>0 占比 —— 因子总体有效性
* 分年平均 IC —— 稳定性（只在牛市有效的因子要打折）

样本过滤：
* 信号日当根 bar 存在且 volume>0（排除停牌）。
* 连续 5 日 close 与 pct_change 均冻结的行视为异常停牌，收益置 NaN
  （复权检查发现的降级源坏数据，如 001331）。
* 复权断裂行（close2close 与源 pct_change 偏离 >0.5%，2026-06/07 除权
  批次拼接所致）当日收益置 NaN，避免假跳空污染动量与前瞻收益。
* 因子所需历史不足（新股）自动 NaN 排除。

已知局限（结果解读时注意）：
* 幸存者偏差——股票池是当前快照，2021-2026 退市股不在内，IC 略偏乐观。
* 未过滤 ST 与涨跌停不可成交样本；未做行业/市值中性化。
* turnover/amount 列 79% 缺失，量类因子基于 volume。

用法：在 backend/ 目录下 ``python scripts/ic_survey.py``。结果打印并写入
``reports/ic_survey_<date>.md``。
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal  # noqa: E402
from app.models.stock import DailyPrice  # noqa: E402

HORIZON = 10          # 前瞻收益窗口（交易日）
STEP = 10             # 调仓间隔 = HORIZON，保证 IC 样本不重叠
MIN_BARS = 60         # 信号日前的最少历史 bar 数
FREEZE_DAYS = 5       # 价格冻结判定窗口
ADJ_TOL = 0.005       # 复权断裂容差


def load_panels() -> dict[str, pd.DataFrame]:
    """从 daily_prices 构造 date × stock 的 OHLCV/pct 面板。"""
    db = SessionLocal()
    try:
        rows = db.execute(
            select(
                DailyPrice.stock_code,
                DailyPrice.trade_date,
                DailyPrice.open,
                DailyPrice.high,
                DailyPrice.low,
                DailyPrice.close,
                DailyPrice.volume,
                DailyPrice.pct_change,
            )
        ).all()
    finally:
        db.close()

    df = pd.DataFrame(
        rows,
        columns=["stock_code", "trade_date", "open", "high", "low", "close", "volume", "pct_change"],
    )
    for col in ("open", "high", "low", "close", "volume", "pct_change"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    panels: dict[str, pd.DataFrame] = {}
    index = None
    for col in ("open", "high", "low", "close", "volume", "pct_change"):
        wide = df.pivot(index="trade_date", columns="stock_code", values=col)
        if index is None:
            index = wide.index
        else:  # 对齐到同一行列框架（缺失组合本来就是 NaN）
            wide = wide.reindex(index=index)
        panels[col] = wide.sort_index()
    return panels


def clean_returns(p: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """构造可信的日收益面板：剔除复权断裂行与价格冻结行。"""
    close, pct = p["close"], p["pct_change"]
    ret = close.pct_change()

    # 复权断裂：close2close 与源涨跌幅偏离过大
    broken = (ret - pct / 100.0).abs() > ADJ_TOL
    broken &= ret.notna() & pct.notna()

    # 价格冻结：FREEZE_DAYS 日内 close 与 pct_change 都不动（降级源坏数据）
    frozen_close = close == close.shift(1)
    frozen_pct = (pct == 0) | pct.isna()
    frozen = frozen_close & frozen_pct
    frozen = frozen.rolling(FREEZE_DAYS, min_periods=FREEZE_DAYS).min() == 1

    bad = broken | frozen
    print(
        f"数据清洗: 复权断裂 {int(broken.to_numpy().sum()):,} 格, "
        f"价格冻结 {int((frozen & ~broken).to_numpy().sum()):,} 格 -> 日收益置 NaN"
    )
    return ret.mask(bad)


def build_factors(p: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """向量化因子面板。公式与 app.factor 注册因子一致；标注 *_n 的为归一化变体。"""
    close, high, low, vol = p["close"], p["high"], p["low"], p["volume"]
    f: dict[str, pd.DataFrame] = {}

    # --- 动量 / 反转 ---
    for n in (5, 12, 20, 60, 120):
        f[f"roc{n}"] = close / close.shift(n) - 1.0
    # skip-month 动量：过去 12 个月收益去掉最近 1 个月
    f["mom_12_1"] = close.shift(20) / close.shift(240) - 1.0

    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    ag = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    al = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = ag / al.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    f["rsi14"] = rsi.mask(al == 0, 100.0)

    # --- 趋势（价格水平因子归一化为跨股票可比） ---
    ma5, ma20, ma60 = close.rolling(5).mean(), close.rolling(20).mean(), close.rolling(60).mean()
    f["dist_ma20"] = close / ma20 - 1.0
    f["ma5_ma20"] = ma5 / ma20 - 1.0
    f["ma20_ma60"] = ma20 / ma60 - 1.0
    # MACD DIF 归一化
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    f["macd_dif_n"] = (ema12 - ema26) / close
    # ADX14（Wilder）
    up, dn = high.diff(), -low.diff()
    plus_dm = up.where((up > dn) & (up > 0), 0.0)
    minus_dm = dn.where((dn > up) & (dn > 0), 0.0)
    tr = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1
    ).max(axis=1)
    def _wilder(s: pd.DataFrame | pd.Series) -> pd.Series:
        return s.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    atr14 = _wilder(tr)
    # 注意：DataFrame 与 Series 运算必须 div(axis=0) 按行广播，
    # 默认的列对齐会把日期 index 对到股票 columns 上得到全 NaN。
    plus_di = 100 * _wilder(plus_dm).div(atr14, axis=0)
    minus_di = 100 * _wilder(minus_dm).div(atr14, axis=0)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    f["adx14"] = _wilder(dx)

    # --- 波动率 ---
    logret = np.log(close / close.shift(1))
    f["hv20"] = logret.rolling(20).std() * np.sqrt(250)
    f["atr14_n"] = close.rdiv(atr14, axis=0)
    mid = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    f["boll_width"] = 4 * std20 / mid
    f["skew20"] = logret.rolling(20).skew()

    # --- 量 / 流动性 ---
    vma5 = vol / vol.rolling(5).mean().shift(1)
    vma10 = vol / vol.rolling(10).mean().shift(1)
    f["vol_ratio5"] = vma5
    f["vol_ratio10"] = vma10
    # OBV 趋势方向：最近 5 bar OBV 斜率符号
    obv_dir = np.sign(close.diff()).replace(0.0, 0.0)
    obv = (obv_dir * vol).fillna(0.0).cumsum()
    f["obv_trend"] = np.sign(obv - obv.shift(5))
    # 量价同向度（6 日窗口）
    pc = close / close.shift(5) - 1.0
    vc = vol / vol.shift(5) - 1.0
    f["vol_price_trend"] = np.sign(pc) * np.sign(vc)
    # 20 日日均成交额（元）——流动性代理（amount 列 79% 缺失）
    f["amt20"] = (close * vol).rolling(20).mean()
    # 距 52 周新高
    f["high_252"] = close / close.rolling(252, min_periods=120).max() - 1.0

    return f


def rowwise_rank_corr(x: pd.DataFrame, y: pd.DataFrame) -> pd.Series:
    """逐行 Spearman 相关（秩的 Pearson），比循环 spearmanr 快两个量级。"""
    xr = x.rank(axis=1)
    yr = y.rank(axis=1)
    xm = xr.sub(xr.mean(axis=1), axis=0)
    ym = yr.sub(yr.mean(axis=1), axis=0)
    cov = (xm * ym).sum(axis=1)
    denom = np.sqrt((xm**2).sum(axis=1) * (ym**2).sum(axis=1))
    return (cov / denom).where(denom > 0)


def main() -> None:
    print("加载面板...")
    p = load_panels()
    close, vol = p["close"], p["volume"]
    print(f"面板: {close.shape[0]} 交易日 × {close.shape[1]} 股票")

    ret = clean_returns(p)
    factors = build_factors(p)

    # 前瞻收益：从信号日 t 收盘持有到 t+HORIZON（用可信日收益复利）
    fwd = (1.0 + ret).rolling(HORIZON).apply(lambda r: np.prod(r), raw=True).shift(-HORIZON)

    # 调仓日：每 STEP 个交易日，留足前置历史
    dates = close.index
    rebalance = dates[MIN_BARS::STEP]
    rebalance = [d for d in rebalance if d in fwd.index][: -0 or None]
    # 最后一根 bar 之后没有完整前瞻窗口的调仓日会在 fwd 上自然是 NaN，无需特判

    # 可交易样本 mask：信号日当根 bar 存在且未停牌
    tradable = close.notna() & (vol > 0)
    hist_count = close.notna().cumsum()
    enough = hist_count >= MIN_BARS

    ic_rows: dict[str, pd.Series] = {}
    n_samples = []
    for code, fac in factors.items():
        m = tradable & enough
        fv = fac.loc[rebalance].where(m.loc[rebalance])
        fr = fwd.loc[rebalance].where(m.loc[rebalance])
        ic = rowwise_rank_corr(fv, fr).dropna()
        ic_rows[code] = ic
        if code == next(iter(factors)):
            n_samples = fv.notna().sum(axis=1)

    ics = pd.DataFrame(ic_rows)
    ics.index.name = "rebalance_date"
    n_samples = pd.Series(n_samples, index=ics.index, name="n_stocks")

    # ---- 汇总 ----
    def summarize(s: pd.Series) -> dict:
        s = s.dropna()
        if len(s) < 12:
            return {}
        ir = s.mean() / s.std() if s.std() > 0 else np.nan
        return {
            "mean_ic": round(s.mean(), 4),
            "ic_std": round(s.std(), 4),
            "icir": round(ir, 3),
            "t_stat": round(s.mean() / s.std() * np.sqrt(len(s)), 2) if s.std() > 0 else np.nan,
            "ic_pos%": round((s > 0).mean(), 3),
            "n_dates": int(len(s)),
        }

    summary = pd.DataFrame({k: summarize(v) for k, v in ics.items()}).T
    summary = summary.sort_values("icir", key=lambda s: s.abs(), ascending=False)

    by_year = ics.groupby(ics.index.year).mean().T.round(4)
    by_year.columns = [f"{c}年IC" for c in by_year.columns]

    report = pd.concat([summary, by_year], axis=1)
    print(f"\n=== RankIC 普查 (horizon={HORIZON}d, 每{STEP}日调仓, "
          f"{ics.index.min().date()} ~ {ics.index.max().date()}, 平均每期 {int(n_samples.mean()):,} 只) ===")
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(report.to_string())

    out_dir = Path(__file__).resolve().parents[1] / "reports"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"ic_survey_{date.today():%Y%m%d}.md"
    lines = [
        f"# 全市场因子 RankIC 普查（{date.today():%Y-%m-%d}）",
        "",
        f"- 样本: {close.shape[1]} 只股票, {ics.index.min().date()} ~ {ics.index.max().date()}, "
        f"horizon={HORIZON} 日, 每 {STEP} 个交易日调仓",
        f"- 平均每期股票数: {int(n_samples.mean()):,}",
        "",
        "## 因子汇总（按 |ICIR| 降序）",
        "",
        report.to_markdown(),
        "",
        "## 说明",
        "- 已剔除复权断裂行与价格冻结行的收益；未过滤 ST/涨跌停；存在幸存者偏差。",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    ics.to_csv(out_dir / f"ic_by_date_{date.today():%Y%m%d}.csv", encoding="utf-8")
    print(f"\n报告已写入 {out}")


if __name__ == "__main__":
    main()
