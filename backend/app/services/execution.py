"""Shared execution layer (V3a BP-V3a-003 prerequisite).

Single source of truth for the next-open fill semantics used by the
multi-factor portfolio backtest and, later, the V3a paper-trading engine:

- per-date open-price / tradability lookups (missing trade-status rows are
  permissive: absent code → tradable, exactly like the backtest loop);
- slipped fill price at the open (buys pay up, sells receive less);
- A-share board-lot sizing (100 shares, floored);
- itemized fee breakdown whose total is same-source with
  :class:`app.services.cost_model.CostParams` (min-5 commission both sides,
  stamp duty on sells only, transfer fee on both sides);
- the pure fill decision: unfillable BUYs are skipped for the cycle,
  unfillable SELLs are deferred (position carried).

These helpers were extracted verbatim from
``portfolio_backtest_service.run_mf_backtest``'s matching loop — that loop now
calls into this module with no behavioural change.
"""

from __future__ import annotations

import math
from datetime import date, datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.kline import SaDailyTradeStatus
from app.models.stock import DailyPrice
from app.services.cost_model import CostParams

BOARD_LOT = 100  # A-share board lot (shares)

#: fill decision outcomes
ACTION_FILL = "fill"
ACTION_SKIP = "skip"  # buy cannot fill → drop it for this cycle
ACTION_DEFER = "defer"  # sell cannot fill → carry the position


def _as_date(d: date | datetime | pd.Timestamp) -> date:
    """Normalize ``date`` / ``datetime`` / ``pd.Timestamp`` to ``date``."""
    if isinstance(d, pd.Timestamp) or isinstance(d, datetime):
        return d.date()
    return d


def fetch_open_prices(
    db: Session, codes: list[str], d: date | pd.Timestamp
) -> dict[str, float]:
    """Open prices of ``codes`` on trade date ``d``.

    Codes with no row or a NULL open are simply absent from the result —
    the matching loop treats "no open price" as "cannot trade today".
    """
    if not codes:
        return {}
    rows = db.execute(
        select(DailyPrice.stock_code, DailyPrice.open)
        .where(
            DailyPrice.trade_date == _as_date(d),
            DailyPrice.stock_code.in_(codes),
            DailyPrice.open.is_not(None),
        )
    ).all()
    return {c: float(o) for c, o in rows}


def fetch_tradability(
    db: Session, codes: list[str], d: date | pd.Timestamp
) -> tuple[dict[str, bool], dict[str, bool]]:
    """``(sell_ok, buy_ok)`` permission maps from ``sa_daily_trade_status``.

    Missing rows are permissive: a code absent from a map is tradable, so
    callers resolve with ``map.get(code, True)`` (current backtest behaviour).
    """
    if not codes:
        return {}, {}
    rows = db.execute(
        select(
            SaDailyTradeStatus.stock_code,
            SaDailyTradeStatus.sell_tradable,
            SaDailyTradeStatus.buy_tradable,
        ).where(
            SaDailyTradeStatus.stock_code.in_(codes),
            SaDailyTradeStatus.trade_date == _as_date(d),
        )
    ).all()
    sell_ok = {c: s != 0 for c, s, _b in rows}
    buy_ok = {c: b != 0 for c, _s, b in rows}
    return sell_ok, buy_ok


def fill_price(open_price: float, side: str, slippage: float) -> float:
    """Execution price at the open with percentage slippage applied.

    buy → ``open * (1 + s)`` (pay up); sell → ``open * (1 - s)`` (receive
    less) — the exact arithmetic of the portfolio backtest matching loop.
    """
    if side == "buy":
        return open_price * (1.0 + slippage)
    if side == "sell":
        return open_price * (1.0 - slippage)
    raise ValueError(f"side must be 'buy' | 'sell', got {side!r}")


def board_lot_shares(target_value: float, price: float) -> int:
    """Shares affordable within ``target_value``, floored to whole lots.

    A whole number of 100-share board lots: ``int(target / price / 100) * 100``.
    An invalid or non-positive price (or amount too small for one lot)
    yields 0 — the caller then skips the order.
    """
    if price is None or not math.isfinite(price) or price <= 0:
        return 0
    lots = int(target_value / price / BOARD_LOT)
    return max(lots, 0) * BOARD_LOT


def cost_breakdown(side: str, amount: float, params: CostParams) -> dict:
    """Itemized fees for one fill of ``amount`` CNY.

    - ``commission``: both sides, ``max(amount * rate, min_commission)``;
    - ``stamp_duty``: sell side only;
    - ``transfer_fee``: both sides;
    - ``total``: re-uses ``CostParams.buy_cost`` / ``sell_cost`` directly, so
      the breakdown is same-source with the cost model by construction.
    """
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' | 'sell', got {side!r}")
    commission = max(amount * params.commission_rate, params.min_commission)
    stamp_duty = amount * params.stamp_duty_rate if side == "sell" else 0.0
    transfer_fee = amount * params.transfer_fee_rate
    total = params.sell_cost(amount) if side == "sell" else params.buy_cost(amount)
    return {
        "commission": commission,
        "stamp_duty": stamp_duty,
        "transfer_fee": transfer_fee,
        "total": total,
    }


def match_fill(
    side: str, open_price: float | None, tradable: bool
) -> tuple[str, str]:
    """Pure fill decision → ``(action, reason)``.

    action is ``'fill'`` when the order can trade at ``open_price``,
    ``'skip'`` for a blocked buy (dropped for this cycle), ``'defer'`` for a
    blocked sell (position carried) — the skip/defer asymmetry of the
    portfolio backtest loop (一字涨停/停牌 buys are skipped, 一字跌停/停牌
    or price-less sells are carried).
    """
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' | 'sell', got {side!r}")
    blocked_action = ACTION_SKIP if side == "buy" else ACTION_DEFER
    if open_price is None or not math.isfinite(open_price) or open_price <= 0:
        return blocked_action, "no_open_price"
    if not tradable:
        return blocked_action, f"{side}_blocked"
    return ACTION_FILL, ""
