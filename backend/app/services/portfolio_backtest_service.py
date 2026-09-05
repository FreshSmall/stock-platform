"""Multi-factor portfolio backtest (V2.2 BP-V2.2-005 / T2.5).

Periodic-rebalance, vectorized-panel portfolio simulation:

    rebalance date t (week/month/N-day end)
        → scoped universe (PIT ∩ sample filters ∩ buy_tradable ∩ liquidity top-K)
        → unified multi-factor score (direction + neutralize, z-score pipeline)
        → top-N target portfolio
    execution on t+1 OPEN (T+1)
        → sell non-targets (sell_tradable=0 → carry the position)
        → buy new targets (buy_tradable=0 → skip this cycle)
        → A-share cost model on every fill (commission min 5 / stamp on sell /
          transfer fee / slippage on the fill price)
    daily NAV = cash + Σ shares × close (ffilled close for suspended names)

Runs are persisted through the shared ``sa_backtest_run``/``sa_backtest_result``
tables with ``strategy='mf_portfolio'`` — queryable via the existing
``GET /backtest/{run_id}`` endpoint.

Panel-computable factors only (the scoring path is the vectorized one);
snapshot factors (pe/pb/…) are rejected up front.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.factor import multi_factor
from app.factor.multi_factor import FactorWeight
from app.models.backtest import SaBacktestResult, SaBacktestRun
from app.models.kline import SaDailyTradeStatus
from app.models.stock import DailyPrice
from app.services import factor_panel, factor_service
from app.services.cost_model import CostParams
from app.services.factor_panel import MarketPanel

logger = logging.getLogger(__name__)

TRADING_DAYS = 250
BOARD_LOT = 100  # A-share board lot


def _resolve_specs(
    factors: list[dict] | None, preset: str | None
) -> list[FactorWeight]:
    if (not factors) and preset:
        specs = multi_factor.resolve_preset(preset)
        if specs is None:
            raise ValueError(f"unknown preset: {preset}")
        return specs
    specs = factor_service._normalize_specs(factors or [])
    if not specs:
        raise ValueError("no factors specified")
    unsupported = [s.code for s in specs if s.code not in factor_panel.PANEL_FACTORS]
    if unsupported:
        raise ValueError(
            f"组合回测仅支持面板化因子，以下不支持: {unsupported}"
        )
    return specs


def _rebalance_schedule(
    panel: MarketPanel, start: date, end: date, freq: str
) -> list[pd.Timestamp]:
    """Rebalance dates: last trade day of each ISO week / month, or every N days."""
    idx = panel.dates
    in_range = idx[(idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))]
    if in_range.empty:
        return []
    if freq == "W":
        grp = in_range.to_series().groupby(in_range.isocalendar().year.astype(str) + "-" + in_range.isocalendar().week.astype(str))
        return list(grp.last().sort_values())
    if freq == "M":
        grp = in_range.to_series().groupby(in_range.to_period("M"))
        return list(grp.last().sort_values())
    try:
        n = int(freq)
    except ValueError:
        raise ValueError(f"freq 必须是 'W' | 'M' | 整数交易日, got {freq!r}")
    if n < 1:
        raise ValueError("freq: N-day 间距必须 ≥ 1")
    return list(in_range[::n])


def _opens_on(db: Session, codes: list[str], d: pd.Timestamp) -> dict[str, float]:
    rows = db.execute(
        select(DailyPrice.stock_code, DailyPrice.open)
        .where(
            DailyPrice.trade_date == d.date(),
            DailyPrice.stock_code.in_(codes),
            DailyPrice.open.is_not(None),
        )
    ).all()
    return {c: float(o) for c, o in rows}


def _sellable_on(
    db: Session, codes: list[str], d: pd.Timestamp
) -> dict[str, bool]:
    """sell permission at ``d`` from sa_daily_trade_status (missing → True)."""
    rows = db.execute(
        select(SaDailyTradeStatus.stock_code, SaDailyTradeStatus.sell_tradable)
        .where(
            SaDailyTradeStatus.stock_code.in_(codes),
            SaDailyTradeStatus.trade_date == d.date(),
        )
    ).all()
    return {c: v != 0 for c, v in rows}


def _buyable_on(
    db: Session, codes: list[str], d: pd.Timestamp
) -> dict[str, bool]:
    rows = db.execute(
        select(SaDailyTradeStatus.stock_code, SaDailyTradeStatus.buy_tradable)
        .where(
            SaDailyTradeStatus.stock_code.in_(codes),
            SaDailyTradeStatus.trade_date == d.date(),
        )
    ).all()
    return {c: v != 0 for c, v in rows}


def _score_targets(
    db: Session,
    factor_values: dict[str, pd.Series],  # code → cross-section at date t
    specs: list[FactorWeight],
    codes: list[str],
    t: pd.Timestamp,
    neutralize: str,
    top_n: int,
) -> list[str]:
    """Unified scoring on one date's scoped cross-section → top-N codes."""
    frame = pd.DataFrame(factor_values).reindex(codes)
    if frame.empty:
        return []
    if neutralize != "none":
        for col in frame.columns:
            frame[col] = factor_service._neutralize_series(
                db, frame[col], t.date(), neutralize
            )
    weights = {s.code: s.weight for s in specs}
    directions = {s.code: s.direction for s in specs}
    score = multi_factor.composite_score(frame, weights, directions)
    return list(score.head(top_n).index)


def run_mf_backtest(
    db: Session,
    *,
    factors: list[dict] | None = None,
    preset: str | None = None,
    start: date,
    end: date,
    freq: str = "W",
    top_n: int = 10,
    initial_cash: float = 100_000.0,
    benchmark: str = "sh000001",
    pool: str = "current",
    exclude_st: bool = False,
    exclude_suspended: bool = False,
    only_tradable: bool = True,
    neutralize: str = "none",
    liquidity_top_k: int = 1000,
    cost: dict | None = None,
    user_id: int | None = None,
) -> dict:
    """Run + persist a multi-factor portfolio backtest; returns the run payload."""
    specs = _resolve_specs(factors, preset)
    cp = CostParams(**(cost or {}))
    factor_codes = [s.code for s in specs]

    data_end = end + timedelta(days=15)
    panel = factor_panel.load_market_panel(db, start, data_end)
    if panel.close.empty:
        raise ValueError("区间内无行情数据")
    codes_all = factor_panel.select_universe(
        db, panel, start, end, pool=pool, universe_size=liquidity_top_k
    )
    if not codes_all:
        raise ValueError("股票池为空")
    panel = MarketPanel(
        close=panel.close.reindex(columns=codes_all),
        volume=panel.volume.reindex(columns=codes_all),
    )

    fp_values = {fc: factor_panel.panel_factor_values(panel, fc) for fc in factor_codes}
    amt_panel = fp_values.get("amt20")
    if amt_panel is None:
        amt_panel = factor_panel.panel_factor_values(panel, "amt20")

    listed = (
        factor_panel.pit_listed_mask(db, panel, codes_all) if pool == "pit" else None
    )
    status = factor_panel.load_trade_status(db, panel, codes_all)

    reb_dates = _rebalance_schedule(panel, start, end, freq)
    if len(reb_dates) < 2:
        raise ValueError(f"区间太短：仅 {len(reb_dates)} 个调仓日（freq={freq}）")

    all_dates = panel.dates
    nav_dates = all_dates[
        (all_dates >= reb_dates[0]) & (all_dates <= pd.Timestamp(end))
    ]

    # --- pass 1: targets per rebalance date (scope + score, no execution) ---
    targets_by_t: dict[pd.Timestamp, list[str]] = {}
    for t in reb_dates[:-1]:
        first = fp_values[factor_codes[0]]
        if t not in first.index:
            continue
        row0 = first.loc[t]
        candidates = [c for c in codes_all if pd.notna(row0.get(c))]
        if not candidates:
            continue
        amt_row = amt_panel.loc[t] if t in amt_panel.index else None
        if liquidity_top_k and amt_row is not None:
            amt = amt_row.reindex(candidates).dropna().sort_values(ascending=False)
            candidates = list(amt.head(liquidity_top_k).index)
        if listed is not None and t in listed.index:
            ok = listed.loc[t].reindex(candidates).fillna(False)
            candidates = [c for c in candidates if bool(ok.get(c, False))]
        if status is not None:
            if exclude_st and status.is_st is not None and t in status.is_st.index:
                bad = status.is_st.loc[t].reindex(candidates).fillna(False)
                candidates = [c for c in candidates if not bool(bad.get(c, False))]
            if (
                exclude_suspended
                and status.is_suspended is not None
                and t in status.is_suspended.index
            ):
                bad = status.is_suspended.loc[t].reindex(candidates).fillna(False)
                candidates = [c for c in candidates if not bool(bad.get(c, False))]
            if (
                only_tradable
                and status.not_tradable is not None
                and t in status.not_tradable.index
            ):
                bad = status.not_tradable.loc[t].reindex(candidates).fillna(False)
                candidates = [c for c in candidates if not bool(bad.get(c, False))]
        if len(candidates) < top_n:
            continue
        cross = {fc: fp_values[fc].loc[t] for fc in factor_codes}
        targets = _score_targets(db, cross, specs, candidates, t, neutralize, top_n)
        if targets:
            targets_by_t[t] = targets

    # --- pass 2: single chronological walk — execute at t+1 open, mark to close ---
    cash = float(initial_cash)
    holdings: dict[str, int] = {}
    last_close: dict[str, float] = {}
    rebalance_records: list[dict] = []
    holdings_timeline: list[dict] = []
    turnover_series: list[dict] = []
    total_cost = 0.0
    exec_index = {d: i for i, d in enumerate(all_dates)}
    exec_of = {}  # t_exec -> t (rebalance signal date)
    for t in targets_by_t:
        i = exec_index[t]
        if i + 1 < len(all_dates):
            exec_of[all_dates[i + 1]] = t

    closes = panel.close.ffill()
    nav_series: list[float] = []
    nav_points: list[dict] = []
    dd_points: list[dict] = []
    peak = -1.0

    for d in nav_dates:
        # execution day: apply this rebalance's trades at today's open
        t = exec_of.get(d)
        if t is not None:
            targets = targets_by_t[t]
            involved = list(set(targets) | set(holdings))
            opens = _opens_on(db, involved, d)
            sell_ok = _sellable_on(db, involved, d)
            buy_ok = _buyable_on(db, involved, d)
            nav_before = cash + sum(
                sh * opens.get(c, last_close.get(c, 0.0))
                for c, sh in holdings.items()
            )
            buys: list[dict] = []
            sells: list[dict] = []
            reb_cost = 0.0

            for c in list(holdings):  # sells first (free cash)
                if c in targets or c not in opens:
                    continue
                if not sell_ok.get(c, True):
                    continue  # blocked (一字跌停/停牌) → carry
                px = opens[c] * (1.0 - cp.slippage_rate)
                shares = holdings.pop(c)
                amount = shares * px
                fee = cp.sell_cost(amount)
                cash += amount - fee
                reb_cost += fee
                sells.append(
                    {"code": c, "shares": shares, "price": round(px, 4), "fee": round(fee, 2)}
                )

            per_target = max(
                cash
                + sum(sh * opens.get(c, 0.0) for c, sh in holdings.items()),
                0.0,
            ) / top_n
            for c in targets:  # buys: equal-weight slots at slipped open
                if c in holdings or c not in opens:
                    continue
                if not buy_ok.get(c, True):
                    continue  # blocked (一字涨停) → skip this cycle
                px = opens[c] * (1.0 + cp.slippage_rate)
                lots = int(per_target / px / BOARD_LOT)
                shares = lots * BOARD_LOT
                if shares <= 0:
                    continue
                amount = shares * px
                fee = cp.buy_cost(amount)
                if amount + fee > cash:
                    continue
                cash -= amount + fee
                holdings[c] = shares
                reb_cost += fee
                buys.append(
                    {"code": c, "shares": shares, "price": round(px, 4), "fee": round(fee, 2)}
                )

            total_cost += reb_cost
            traded_value = sum(b["shares"] * b["price"] for b in buys) + sum(
                s["shares"] * s["price"] for s in sells
            )
            rebalance_records.append(
                {
                    "rebalance_date": t.date().isoformat(),
                    "exec_date": d.date().isoformat(),
                    "target": targets,
                    "buys": buys,
                    "sells": sells,
                    "cost": round(reb_cost, 2),
                }
            )
            if nav_before > 0:
                turnover_series.append(
                    {
                        "date": d.date().isoformat(),
                        "turnover": round(traded_value / (2.0 * nav_before), 4),
                    }
                )

        # mark to market at close
        row = closes.loc[d]
        mv = 0.0
        for c, sh in holdings.items():
            px = row.get(c)
            if pd.notna(px):
                last_close[c] = float(px)
            mv += sh * last_close.get(c, 0.0)
        nav = cash + mv
        nav_series.append(nav)
        nav_points.append({"date": d.date().isoformat(), "value": round(nav, 2)})
        peak = max(peak, nav)
        dd_points.append(
            {"date": d.date().isoformat(), "drawdown": round((nav / peak - 1.0) * 100, 4)}
        )
        if d in targets_by_t or d == nav_dates[-1]:
            holdings_timeline.append(
                {
                    "date": d.date().isoformat(),
                    "holdings": [
                        {"code": c, "shares": sh} for c, sh in sorted(holdings.items())
                    ],
                }
            )

    if len(nav_series) < 2:
        raise ValueError("NAV 序列过短，无法计算指标")

    nav_arr = np.array(nav_series)
    rets = nav_arr[1:] / nav_arr[:-1] - 1.0
    total_return = float(nav_arr[-1] / nav_arr[0] - 1.0)
    n_years = len(nav_series) / TRADING_DAYS
    ann_return = (
        float(nav_arr[-1] / nav_arr[0]) ** (1.0 / n_years) - 1.0
        if n_years >= 0.05
        else None
    )
    vol = float(np.std(rets, ddof=1) * math.sqrt(TRADING_DAYS)) if len(rets) > 1 else None
    sharpe = (
        float(np.mean(rets) / np.std(rets, ddof=1) * math.sqrt(TRADING_DAYS))
        if len(rets) > 1 and np.std(rets, ddof=1) > 0
        else None
    )
    peak = np.maximum.accumulate(nav_arr)
    max_dd = float((nav_arr / peak - 1.0).min())
    calmar = (
        ann_return / abs(max_dd)
        if ann_return is not None and max_dd < -1e-9
        else None
    )

    # benchmark NAV (index close, fallback: equal-weight universe)
    bm_curve = _benchmark_curve(db, str(benchmark), nav_dates, panel)
    bm_return = (
        float(bm_curve[-1]["value"] / bm_curve[0]["value"] - 1.0)
        if bm_curve
        else None
    )

    payload = {
        "run_config": {
            "factors": [
                {"code": s.code, "weight": s.weight, "direction": s.direction}
                for s in specs
            ],
            "freq": freq,
            "top_n": top_n,
            "pool": pool,
            "neutralize": neutralize,
            "liquidity_top_k": liquidity_top_k,
            "exclude_st": exclude_st,
            "exclude_suspended": exclude_suspended,
            "only_tradable": only_tradable,
            "cost": {
                "commission_rate": cp.commission_rate,
                "min_commission": cp.min_commission,
                "stamp_duty_rate": cp.stamp_duty_rate,
                "transfer_fee_rate": cp.transfer_fee_rate,
                "slippage_rate": cp.slippage_rate,
            },
            "benchmark": str(benchmark),
        },
        "metrics": {
            "total_return": round(total_return, 4),
            "ann_return": round(ann_return, 4),
            "vol": round(vol, 4) if vol is not None else None,
            "sharpe": round(sharpe, 4) if sharpe is not None else None,
            "max_drawdown": round(max_dd, 4),
            "calmar": round(calmar, 4) if calmar is not None else None,
            "benchmark_return": round(bm_return, 4) if bm_return is not None else None,
            "avg_turnover": round(
                float(np.mean([x["turnover"] for x in turnover_series])), 4
            )
            if turnover_series
            else None,
            "total_cost": round(total_cost, 2),
            "n_rebalances": len(rebalance_records),
        },
        "nav": nav_points,
        "drawdown_curve": dd_points,
        "benchmark_curve": bm_curve,
        "turnover_series": turnover_series,
        "holdings_timeline": holdings_timeline,
        "rebalances": rebalance_records,
    }

    run_id = _persist(db, user_id, payload, start, end, float(initial_cash), cp)
    payload["run_id"] = run_id
    return payload


def _benchmark_curve(
    db: Session,
    benchmark: str,
    nav_dates: pd.DatetimeIndex,
    panel: MarketPanel,
) -> list[dict]:
    from app.models.market_data import SaIndexQuote

    rows = db.execute(
        select(SaIndexQuote.trade_date, SaIndexQuote.close).where(
            SaIndexQuote.index_code == benchmark,
            SaIndexQuote.trade_date >= nav_dates[0].date(),
            SaIndexQuote.trade_date <= nav_dates[-1].date(),
        )
    ).all()
    series: pd.Series | None = None
    if rows:
        series = pd.Series(
            {pd.Timestamp(d): float(c) for d, c in rows if c is not None}
        ).sort_index()
    else:
        # fallback: equal-weight universe close index
        k = panel.close.iloc[0].dropna()
        codes = list(k.index[:200])
        series = panel.close[codes].mean(axis=1)
    if series is None or series.empty:
        return []
    series = series[series.index <= nav_dates[-1]]
    aligned = series.reindex(nav_dates, method="ffill").dropna()
    if aligned.empty:
        return []
    base = float(aligned.iloc[0])
    return [
        {"date": d.date().isoformat(), "value": round(float(v) / base, 6)}
        for d, v in aligned.items()
    ]


def _persist(
    db: Session,
    user_id: int | None,
    payload: dict,
    start: date,
    end: date,
    initial_cash: float,
    cp: CostParams,
) -> str:
    run_id = f"bt-{uuid.uuid4().hex[:12]}"
    run = SaBacktestRun(
        run_id=run_id,
        user_id=user_id,
        strategy="mf_portfolio",
        params=payload["run_config"],
        stock_pool=[],
        start_date=start,
        end_date=end,
        initial_cash=Decimal(str(initial_cash)),
        commission=Decimal(str(cp.commission_rate)),
        slippage=Decimal(str(cp.slippage_rate)),
        status="running",
    )
    db.add(run)
    db.commit()

    m = payload["metrics"]
    result = SaBacktestResult(
        run_id=run_id,
        return_rate=m["total_return"] * 100,
        max_drawdown=m["max_drawdown"] * 100,
        sharpe=m["sharpe"],
        win_rate=None,
        calmar=m["calmar"],
        information_ratio=None,
        profit_loss_ratio=None,
        benchmark_return=m["benchmark_return"],
        equity_curve=payload["nav"],
        drawdown_curve=payload.get("drawdown_curve"),
        position_curve=[
            {"date": h["date"], "value": len(h["holdings"])}
            for h in payload["holdings_timeline"]
        ],
        benchmark_curve=payload["benchmark_curve"],
        trades={
            "summary": m,
            "turnover_series": payload["turnover_series"],
            "holdings_timeline": payload["holdings_timeline"],
            "rebalances": payload["rebalances"],
        },
    )
    db.add(result)
    run.status = "done"
    run.finished_at = func.now()
    db.commit()
    return run_id
