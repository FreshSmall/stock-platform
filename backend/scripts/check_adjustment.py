"""复权一致性验证：daily_prices 的 qfq 价格序列是否存在基准断裂。

原理
----
``pct_change`` 列来自数据源（真实的复权后日涨跌幅），而 ``close`` 列是
前复权价格。若整条序列的复权基准一致，则逐日计算的
``close_t / close_{t-1} - 1`` 应与 ``pct_change/100`` 处处吻合。

不一致的典型场景：
* 历史回填（2026-08 前后完成）与每日增量同步分别抓取 qfq 数据，期间某股
  发生分红/送转 → 两个基准在拼接处断裂，close2close 收益在除权日跳空。
* 单源内部数据错误。

输出：可疑行总量、涉及股票数、按月分布（判断断裂发生在历史段还是增量
边界）、差异最大的样例。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.stock import DailyPrice

# close2close 与源 pct_change 的容差：复权基准一致时应为浮点级误差。
TOL = 0.005


def main() -> None:
    db = SessionLocal()
    try:
        rows = db.execute(
            select(
                DailyPrice.stock_code,
                DailyPrice.trade_date,
                DailyPrice.close,
                DailyPrice.pct_change,
            )
        ).all()
    finally:
        db.close()

    df = pd.DataFrame(rows, columns=["stock_code", "trade_date", "close", "pct_change"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    # DB Numeric 列以 Decimal 返回，先统一转 float
    df["close"] = pd.to_numeric(df["close"], errors="coerce").astype(float)
    df["pct_change"] = pd.to_numeric(df["pct_change"], errors="coerce").astype(float)
    df = df.sort_values(["stock_code", "trade_date"])

    grp = df.groupby("stock_code", sort=False)
    df["ret_calc"] = grp["close"].transform(lambda s: s.pct_change())
    df["diff"] = df["ret_calc"] - df["pct_change"] / 100.0

    # 只比较两侧行都存在且 pct_change 非空的行
    valid = df["pct_change"].notna() & df["ret_calc"].notna()
    bad = df[valid & (df["diff"].abs() > TOL)].copy()

    total = int(valid.sum())
    print(f"可比行数: {total:,}  容差: {TOL:.1%}")
    print(f"可疑行数: {len(bad):,}  涉及股票数: {bad['stock_code'].nunique()}")
    if bad.empty:
        print("结论: 未发现复权基准断裂。")
        return

    by_month = bad.groupby(bad["trade_date"].dt.to_period("M")).size()
    print("\n可疑行按月分布:")
    for period, count in by_month.items():
        print(f"  {period}: {count}")

    worst = bad.reindex(bad["diff"].abs().sort_values(ascending=False).index).head(15)
    print("\n差异最大的样例:")
    print(
        worst[["stock_code", "trade_date", "close", "pct_change", "ret_calc", "diff"]]
        .to_string(index=False, float_format=lambda x: f"{x:.4f}")
    )


if __name__ == "__main__":
    main()
