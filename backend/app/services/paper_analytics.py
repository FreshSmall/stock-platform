"""Pure-function analytics layer for the V3a paper-trading engine.

No DB, no I/O, no side effects — plain lists/dicts/dates so the paper
engine (separate module) can import these freely:

    rebalance_dates   calendar semantics mirroring the backtest engine's
                      ``_rebalance_schedule`` (ISO week / month / N-day)
    diff_orders       holdings-vs-target diff → order intents (sells first)
    attribution       excess return split into cost drag / cash drag / selection
    drift_compare     weekly paper-vs-backtest NAV drift table
    holding_jaccard   holding-set overlap score (0..1, empty∩empty = 1)
    alert_eval        pass/warn/fail triage for monitoring metrics
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date


# --- rebalance calendar ------------------------------------------------------


def rebalance_dates(dates: list[date], freq: str) -> list[date]:
    """Last trade day per ISO week ('W') / calendar month ('M'), or every N.

    Mirrors ``portfolio_backtest_service._rebalance_schedule`` on a plain
    ascending list of trade dates. ISO weeks cross calendar-year boundaries
    (2026-12-28 .. 2027-01-03 is a single week, 2026-W53). Any other ``freq``
    must parse as an integer N ≥ 1 → every N-th trade day (``dates[::n]``).
    """
    if not dates:
        return []
    if freq == "W":
        last_by_week: dict[tuple[int, int], date] = {}
        for d in dates:  # ascending → the last assignment per week wins
            iso = d.isocalendar()
            last_by_week[(iso[0], iso[1])] = d
        return [last_by_week[k] for k in sorted(last_by_week)]
    if freq == "M":
        last_by_month: dict[tuple[int, int], date] = {}
        for d in dates:
            last_by_month[(d.year, d.month)] = d
        return [last_by_month[k] for k in sorted(last_by_month)]
    try:
        n = int(freq)
    except ValueError:
        raise ValueError(f"freq 必须是 'W' | 'M' | 整数交易日, got {freq!r}")
    if n < 1:
        raise ValueError("freq: N-day 间距必须 ≥ 1")
    return dates[::n]


# --- order intents -----------------------------------------------------------


@dataclass
class OrderIntent:
    """One intended order for the paper engine to schedule (not yet executed)."""

    stock_code: str
    side: str  # 'buy' | 'sell'
    target_weight: float
    est_shares: int


def diff_orders(
    current: dict[str, int],
    target: dict[str, float],
    last_price: dict[str, float],
    cash: float,
    *,
    rebalance_tol: float = 0.2,
    buffer: float = 0.02,
    lot: int = 100,
) -> list[OrderIntent]:
    """Diff current holdings against target weights → order intents.

    actual weight = (last_price × shares) / total assets, where total assets
    = Σ market value + cash; holdings missing a price count as 0 market
    value but still generate their sell order.

      held, not in target            → sell the entire holding
      in target, not held            → buy
      held and in target, |Δw| ≤ tol → untouched
      held and in target, |Δw| > tol → adjust (overweight → sell all,
                                       underweight → buy up to target)

    Buy sizing: floor(target_amount × (1 − buffer) / price / lot) × lot with
    target_amount = weight × total assets; missing/zero price → 0 shares.
    Result sorted sells-first, then by stock_code (execution order, costs
    etc. are the caller's job).
    """
    mv = {code: last_price.get(code, 0.0) * shares for code, shares in current.items()}
    total = sum(mv.values()) + cash

    def _buy_shares(weight: float, code: str) -> int:
        price = last_price.get(code)
        if not price or price <= 0:
            return 0
        return math.floor(weight * total * (1.0 - buffer) / price / lot) * lot

    intents: list[OrderIntent] = []
    for code in sorted(set(current) | set(target)):
        held = current.get(code, 0)
        weight = target.get(code)
        if held and weight is None:
            intents.append(OrderIntent(code, "sell", 0.0, held))
        elif weight is not None and not held:
            intents.append(OrderIntent(code, "buy", weight, _buy_shares(weight, code)))
        elif held and weight is not None:
            actual = mv[code] / total if total > 0 else 0.0
            if abs(weight - actual) <= rebalance_tol:
                continue
            if actual < weight:
                intents.append(
                    OrderIntent(code, "buy", weight, _buy_shares(weight, code))
                )
            else:
                intents.append(OrderIntent(code, "sell", weight, held))
    intents.sort(key=lambda o: (0 if o.side == "sell" else 1, o.stock_code))
    return intents


# --- performance attribution -------------------------------------------------


def attribution(
    nav: list[float],
    bench: list[float],
    cum_fees: float,
    cash_weights: list[float],
    initial_cash: float,
) -> dict:
    """Split excess return into cost drag, cash drag and selection residual.

    nav/bench are equal-length normalized curves (first value 1.0 or the
    same base); cash_weights[t] is the cash share of total assets on day t
    (day 0 is not counted — no return has accrued yet).

      excess     = nav[-1]/nav[0] − bench[-1]/bench[0]
      cost_drag  = −cum_fees / initial_cash
      cash_drag  = Σ_t cash_weight_t × bench daily return_t
      selection  = excess − cost_drag − cash_drag  (residual)

    Empty sequences or mismatched lengths → an all-zero dict (no raise).
    """
    zero = {"excess": 0.0, "cost_drag": 0.0, "cash_drag": 0.0, "selection": 0.0}
    if (
        not nav
        or not bench
        or len(nav) != len(bench)
        or len(cash_weights) != len(nav)
        or nav[0] == 0
        or bench[0] == 0
    ):
        return zero
    excess = nav[-1] / nav[0] - bench[-1] / bench[0]
    cost_drag = -cum_fees / initial_cash if initial_cash else 0.0
    cash_drag = 0.0
    for t in range(1, len(nav)):
        if bench[t - 1] == 0:
            continue
        cash_drag += cash_weights[t] * (bench[t] / bench[t - 1] - 1.0)
    return {
        "excess": excess,
        "cost_drag": cost_drag,
        "cash_drag": cash_drag,
        "selection": excess - cost_drag - cash_drag,
    }


# --- paper vs backtest drift -------------------------------------------------


def drift_compare(
    paper: list[tuple[date, float]],
    backtest: list[tuple[date, float]],
) -> dict:
    """Weekly paper-vs-backtest NAV drift over the aligned date intersection.

    Dates are aligned on the intersection (ascending); each ISO week's
    return = last NAV / first NAV − 1 within that week. Fewer than two
    distinct weeks → ``weekly`` is an empty list (no error). cum_* are the
    whole-interval cumulative returns on the intersection; max_abs_weekly
    is the largest |weekly diff|.
    """
    p_map = dict(paper)
    b_map = dict(backtest)
    common = sorted(set(p_map) & set(b_map))
    weeks: dict[tuple[int, int], list[date]] = {}
    for d in common:
        iso = d.isocalendar()
        weeks.setdefault((iso[0], iso[1]), []).append(d)

    weekly: list[dict] = []
    if len(weeks) >= 2:
        for iso_year, iso_week in sorted(weeks):
            days = weeks[(iso_year, iso_week)]
            p_ret = p_map[days[-1]] / p_map[days[0]] - 1.0
            b_ret = b_map[days[-1]] / b_map[days[0]] - 1.0
            weekly.append(
                {
                    "week": f"{iso_year}-W{iso_week:02d}",
                    "paper_ret": p_ret,
                    "backtest_ret": b_ret,
                    "diff": p_ret - b_ret,
                }
            )

    def _cum(curve: dict[date, float]) -> float:
        if len(common) < 2 or curve[common[0]] == 0:
            return 0.0
        return curve[common[-1]] / curve[common[0]] - 1.0

    return {
        "weekly": weekly,
        "cum_paper": _cum(p_map),
        "cum_backtest": _cum(b_map),
        "max_abs_weekly": max((abs(w["diff"]) for w in weekly), default=0.0),
    }


# --- holding overlap / alerting ----------------------------------------------


def holding_jaccard(a: set[str], b: set[str]) -> float:
    """|a ∩ b| / |a ∪ b|; two empty sets are identical portfolios → 1.0."""
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def alert_eval(
    value: float | None, warn: float, fail: float, *, lower_is_bad: bool = False
) -> str:
    """Triage a monitoring metric: 'pass' | 'warn' | 'fail'.

    Default is upper-limit semantics (drift, turnover, drawdown exceeded):
    above ``warn`` → 'warn', above ``fail`` → 'fail'; exactly at either
    threshold → 'pass'. With ``lower_is_bad`` the comparisons flip (below
    warn → 'warn', below fail → 'fail') for floor-style metrics such as a
    minimum cash ratio. A missing value cannot be judged → 'warn'.
    """
    if value is None:
        return "warn"
    if lower_is_bad:
        if value < fail:
            return "fail"
        if value < warn:
            return "warn"
    else:
        if value > fail:
            return "fail"
        if value > warn:
            return "warn"
    return "pass"
