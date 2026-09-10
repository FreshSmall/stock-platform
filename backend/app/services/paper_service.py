"""Paper trading engine (V3a / BP-V3a-001~006, ROADMAP 阶段三).

Daily four-segment tick per account (:func:`paper_tick`, scheduled 20:00 after
the 17:30 K-line sync and the 19:00 trade-status sync settle):

    ① match   — fill T-1 pending orders at today's open via the shared
                execution layer (same fills as the portfolio backtest)
    ② signal  — on rebalance dates, scope + score through
                ``portfolio_backtest_service.scope_and_score_targets`` (the
                very function the backtest loop uses) → diff vs holdings →
                T+1 orders
    ③ value   — mark holdings to close (ffill) → position/nav snapshots
    ④ alert   — drawdown / turnover / stuck sells / empty signal /
                incomplete data / corporate-action tripwire → paper_health
                rows in the quality-check tables

Zero-drift by construction: fills, sizing, costs and target selection all
run through the same shared code as ``run_mf_backtest``, so replaying the
engine over a historical window reproduces the backtest NAV (test-locked).

Corporate actions: valuation stays on the legacy qfq ``daily_prices`` series
(same as the backtest), where prices are already continuous across
ex-dates — shares are NOT adjusted (that would double-count). Instead the
adjust-factor table drives a consistency tripwire: on an ex-date the qfq
return must track the raw return; a break means stale qfq data →
``corporate_action`` alert, human review.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.factor import multi_factor
from app.models.kline import SaAdjustFactor, SaKlineDaily
from app.models.market_data import SaIndexQuote
from app.models.paper import (
    SaPaperAccount,
    SaPaperNav,
    SaPaperOrder,
    SaPaperPosition,
    SaPaperTrade,
)
from app.models.quality import SaDataQualityCheck, SaDataQualityRule
from app.models.stock import DailyPrice
from app.services import execution, factor_panel, paper_analytics
from app.services import portfolio_backtest_service as pbs
from app.services.cost_model import CostParams

logger = logging.getLogger(__name__)

CHECK_NAME = "paper_health"

# metric → (warn, fail); seeded into sa_data_quality_rule on first run.
DEFAULT_RULES = [
    ("drawdown", Decimal("0.10"), Decimal("0.20")),
    ("turnover", Decimal("1.00"), Decimal("2.00")),
    ("sell_stuck", Decimal("10"), Decimal("20")),  # calendar days, ≈ trading×1.4
]

# Config defaults — field names align with run_mf_backtest kwargs so the
# drift comparison can replay the account config verbatim.
DEFAULT_CONFIG: dict = {
    "preset": "v2_reversal",
    "factors": None,
    "top_n": 10,
    "freq": "W",
    "initial_cash": 1_000_000.0,
    "benchmark": "sh000001",
    "pool": "current",
    "exclude_st": True,
    "exclude_suspended": True,
    "only_tradable": True,
    "neutralize": "none",
    "liquidity_top_k": 1000,
    "cost": None,
    "rebalance_tol": None,  # None → membership-only diff (backtest semantics)
    "start": None,          # signal panel start; set at account creation
}


def _cfg(account: SaPaperAccount) -> dict:
    cfg = {k: v for k, v in DEFAULT_CONFIG.items()}
    cfg.update(account.config or {})
    return cfg


def _dec(x: float, nd: int = 2) -> Decimal:
    return Decimal(str(round(float(x), nd)))


def _next_weekday(d: date) -> date:
    nd = d + timedelta(days=1)
    while nd.weekday() >= 5:
        nd += timedelta(days=1)
    return nd


# ---------------------------------------------------------------- CRUD


def create_account(db: Session, name: str, config: dict | None = None) -> dict:
    cfg = {k: v for k, v in DEFAULT_CONFIG.items()}
    cfg.update(config or {})
    if cfg.get("factors"):
        cfg["preset"] = None
    else:
        specs = multi_factor.resolve_preset(cfg.get("preset") or "")
        if specs is None:
            raise ValueError(f"未知预设: {cfg.get('preset')!r}")
    cfg["start"] = cfg.get("start") or date.today().isoformat()
    account = SaPaperAccount(
        name=name,
        config=cfg,
        initial_cash=_dec(cfg["initial_cash"]),
        cash=_dec(cfg["initial_cash"]),
    )
    db.add(account)
    db.commit()
    return {"id": account.id, "name": account.name}


def list_accounts(db: Session) -> list[dict]:
    accounts = db.execute(select(SaPaperAccount).order_by(SaPaperAccount.id)).scalars().all()
    out = []
    for a in accounts:
        latest = db.execute(
            select(SaPaperNav)
            .where(SaPaperNav.account_id == a.id)
            .order_by(SaPaperNav.trade_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        open_alerts = db.execute(
            select(SaDataQualityCheck)
            .where(
                SaDataQualityCheck.check_name == CHECK_NAME,
                SaDataQualityCheck.status != "pass",
                SaDataQualityCheck.metric_name.like(f"%:{a.id}"),
            )
            .order_by(SaDataQualityCheck.check_date.desc())
            .limit(5)
        ).scalars().all()
        out.append(
            {
                "id": a.id,
                "name": a.name,
                "status": a.status,
                "config": a.config,
                "latest_nav": float(latest.nav) if latest else None,
                "cum_ret": float(latest.nav) - 1.0 if latest else None,
                "drawdown": float(latest.drawdown) if latest and latest.drawdown is not None else None,
                "open_alerts": len(open_alerts),
            }
        )
    return out


def set_status(db: Session, account_id: int, action: str) -> dict:
    if action not in ("pause", "resume", "stop"):
        raise ValueError(f"未知操作: {action!r}")
    account = db.get(SaPaperAccount, account_id)
    if account is None:
        raise ValueError(f"账户不存在: {account_id}")
    account.status = {"pause": "paused", "resume": "active", "stop": "stopped"}[action]
    db.commit()
    return {"id": account.id, "status": account.status}


def _positions_latest(db: Session, account_id: int, before: date) -> dict[str, tuple[int, float]]:
    """Holdings from the latest snapshot strictly before ``before``."""
    snap_date = db.execute(
        select(SaPaperPosition.trade_date)
        .where(SaPaperPosition.account_id == account_id, SaPaperPosition.trade_date < before)
        .order_by(SaPaperPosition.trade_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if snap_date is None:
        return {}
    rows = db.execute(
        select(SaPaperPosition)
        .where(SaPaperPosition.account_id == account_id, SaPaperPosition.trade_date == snap_date)
    ).scalars().all()
    return {r.stock_code: (int(r.shares), float(r.close_price)) for r in rows}


def _benchmark_close(db: Session, benchmark: str, d: date) -> float | None:
    return db.execute(
        select(SaIndexQuote.close)
        .where(
            SaIndexQuote.index_code == benchmark,
            SaIndexQuote.trade_date <= d,
            SaIndexQuote.close.is_not(None),
        )
        .order_by(SaIndexQuote.trade_date.desc())
        .limit(1)
    ).scalar_one_or_none()


# ---------------------------------------------------------------- tick


def paper_tick_all(db: Session, dates: list[date] | None = None) -> dict:
    """Admin/scheduler entry: run every active account over ``dates``."""
    days = dates or [date.today()]
    accounts = db.execute(
        select(SaPaperAccount).where(SaPaperAccount.status == "active")
    ).scalars().all()
    ticks = fills = alerts = 0
    for a in accounts:
        for d in days:
            res = paper_tick(db, a.id, d)
            ticks += 1 if res.get("status") != "skipped" else 0
            fills += res.get("fills", 0)
            alerts += res.get("alerts", 0)
    return {"accounts": len(accounts), "dates": len(days), "ticks": ticks, "fills": fills, "alerts": alerts}


def paper_tick(db: Session, account_id: int, d: date) -> dict:
    """Advance one account one day (four segments). Idempotent per day."""
    account = db.get(SaPaperAccount, account_id)
    if account is None:
        raise ValueError(f"账户不存在: {account_id}")
    if account.status != "active":
        return {"status": "skipped", "reason": f"account {account.status}"}
    if (
        db.execute(
            select(SaPaperNav.id).where(
                SaPaperNav.account_id == account_id, SaPaperNav.trade_date == d
            ).limit(1)
        ).first()
        is not None
    ):
        return {"status": "already_done"}

    cfg = _cfg(account)
    cp = CostParams(**(cfg.get("cost") or {}))
    initial = float(account.initial_cash)
    cash = float(account.cash)
    holdings: dict[str, int] = {c: sh for c, (sh, _px) in _positions_latest(db, account_id, d).items()}
    last_close: dict[str, float] = {c: px for c, (_sh, px) in _positions_latest(db, account_id, d).items()}
    alert_rows: list[SaDataQualityCheck] = []
    rules = _ensure_rules(db)
    fills = 0

    # --- ① match pending orders at today's open (backtest pass-2 semantics)
    pending = db.execute(
        select(SaPaperOrder)
        .where(
            SaPaperOrder.account_id == account_id,
            SaPaperOrder.status == "pending",
            SaPaperOrder.exec_date <= d,
            SaPaperOrder.signal_date < d,
        )
        .order_by(SaPaperOrder.side.desc(), SaPaperOrder.id)  # sells first
    ).scalars().all()
    traded_value = 0.0
    if pending:
        involved = sorted({o.stock_code for o in pending} | set(holdings))
        opens = execution.fetch_open_prices(db, involved, d)
        sell_ok, buy_ok = execution.fetch_tradability(db, involved, d)
        nav_before = cash + sum(
            sh * opens.get(c, last_close.get(c, 0.0)) for c, sh in holdings.items()
        )
        top_n = int(cfg["top_n"])

        for order in [o for o in pending if o.side == "sell"]:
            op = opens.get(order.stock_code)
            action, reason = execution.match_fill("sell", op, sell_ok.get(order.stock_code, True))
            if action != "fill":
                order.exec_date = _next_weekday(d)  # defer → retry next tick
                order.fail_reason = reason[:64] or None
                continue
            px = execution.fill_price(op, "sell", cp.slippage_rate)
            shares = holdings.pop(order.stock_code, 0)
            if shares <= 0:  # nothing left to sell (snapshot mismatch) → done
                order.status = "cancelled"
                order.fail_reason = "no_position"
                continue
            amount = shares * px
            fees = execution.cost_breakdown("sell", amount, cp)
            cash += amount - fees["total"]
            trade = SaPaperTrade(
                account_id=account_id, order_id=order.id, trade_date=d,
                stock_code=order.stock_code, side="sell",
                price=_dec(px, 4), shares=shares, amount=_dec(amount),
                commission=_dec(fees["commission"], 6),
                stamp_duty=_dec(fees["stamp_duty"], 6),
                transfer_fee=_dec(fees["transfer_fee"], 6),
            )
            db.add(trade)
            db.flush()
            order.status, order.filled_trade_id = "filled", int(trade.id)
            traded_value += amount
            fills += 1

        # buys sized exactly like the backtest: equal-weight slots on the
        # post-sell open-marked NAV
        per_target = max(
            cash + sum(sh * opens.get(c, 0.0) for c, sh in holdings.items()), 0.0
        ) / top_n
        for order in [o for o in pending if o.side == "buy"]:
            op = opens.get(order.stock_code)
            action, reason = execution.match_fill("buy", op, buy_ok.get(order.stock_code, True))
            if action != "fill":
                order.status = "skipped"
                order.fail_reason = reason[:64] or None
                continue
            px = execution.fill_price(op, "buy", cp.slippage_rate)
            shares = execution.board_lot_shares(per_target, px)
            amount = shares * px
            fee = cp.buy_cost(amount)
            if shares <= 0 or amount + fee > cash:
                order.status = "skipped"
                order.fail_reason = "insufficient_cash" if shares > 0 else "below_board_lot"
                continue
            cash -= amount + fee
            holdings[order.stock_code] = shares
            buy_fees = execution.cost_breakdown("buy", amount, cp)
            trade = SaPaperTrade(
                account_id=account_id, order_id=order.id, trade_date=d,
                stock_code=order.stock_code, side="buy",
                price=_dec(px, 4), shares=shares, amount=_dec(amount),
                commission=_dec(buy_fees["commission"], 6),
                stamp_duty=_dec(0.0, 6),
                transfer_fee=_dec(buy_fees["transfer_fee"], 6),
            )
            db.add(trade)
            db.flush()
            order.status, order.filled_trade_id = "filled", int(trade.id)
            traded_value += amount
            fills += 1

        if traded_value > 0 and nav_before > 0:
            turnover = traded_value / (2.0 * nav_before)
            warn, fail = rules["turnover"]
            alert_rows.append(_check_row(account_id, d, "turnover", turnover, warn, fail))

    # corporate-action tripwire on held names (see module docstring)
    alert_rows.extend(_corporate_action_alerts(db, account_id, d, holdings, last_close))

    # today's closes, fetched before the signal pass so diff_orders sees real
    # weights; valuation below reuses the updated last_close map
    closes = _closes_on(db, list(holdings), d)
    for c, px in closes.items():
        last_close[c] = px

    # --- ② signal on rebalance dates (panel built only when needed — a full
    # market panel per tick would be minutes over a replay window)
    signal_msg = "no_rebalance"
    cfg_start = date.fromisoformat(cfg["start"])
    trade_dates = [
        r for r in db.execute(
            select(DailyPrice.trade_date)
            .where(
                DailyPrice.trade_date >= cfg_start,
                DailyPrice.trade_date <= max(d, date.today()),
            )
            .distinct()
            .order_by(DailyPrice.trade_date)
        ).scalars()
    ]
    if _qualifies_rebalance(d, trade_dates, cfg["freq"], date.today()):
        panel = None
        try:
            panel = factor_panel.load_market_panel(db, cfg_start, d)
        except Exception:  # noqa: BLE001 - a tick must never crash the scheduler
            logger.exception("paper_tick %s: panel load failed", account_id)
        if panel is not None and not panel.close.empty:
            if _data_incomplete(db, d):
                alert_rows.append(
                    _check_row(account_id, d, "data_incomplete", 1, Decimal("0"), Decimal("1"))
                )
                signal_msg = "data_incomplete"
            else:
                targets = _signal_targets(db, cfg, panel, d)
                if not targets:
                    alert_rows.append(
                        _check_row(account_id, d, "signal_empty", 1, Decimal("0"), Decimal("1"))
                    )
                    signal_msg = "signal_empty"
                else:
                    n_orders = _create_orders(
                        db, account_id, d, holdings, targets, cfg, last_close, cash
                    )
                    signal_msg = f"orders={n_orders}"

    # stuck sells: pending sells older than the warn window
    stuck = db.execute(
        select(SaPaperOrder)
        .where(
            SaPaperOrder.account_id == account_id,
            SaPaperOrder.status == "pending",
            SaPaperOrder.side == "sell",
            SaPaperOrder.signal_date <= d - timedelta(days=14),
        )
        .limit(1)
    ).scalar_one_or_none()
    if stuck is not None:
        age = (d - stuck.signal_date).days
        warn, fail = rules["sell_stuck"]
        alert_rows.append(_check_row(account_id, d, "sell_stuck", age, warn, fail))

    # --- ③ mark to close → position + nav snapshots
    market_value = 0.0
    for c, sh in holdings.items():
        market_value += sh * last_close.get(c, 0.0)
        db.add(
            SaPaperPosition(
                account_id=account_id, trade_date=d, stock_code=c,
                shares=sh, close_price=_dec(last_close.get(c, 0.0), 4),
                market_value=_dec(sh * last_close.get(c, 0.0)),
            )
        )
    total_asset = cash + market_value
    prev = db.execute(
        select(SaPaperNav)
        .where(SaPaperNav.account_id == account_id, SaPaperNav.trade_date < d)
        .order_by(SaPaperNav.trade_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    prev_total = float(prev.total_asset) if prev else initial
    prev_peak = float(
        db.execute(
            select(func.max(SaPaperNav.nav)).where(
                SaPaperNav.account_id == account_id, SaPaperNav.trade_date < d
            )
        ).scalar()
        or 1.0
    )
    nav = total_asset / initial
    peak = max(prev_peak, nav)
    bench = _benchmark_close(db, str(cfg["benchmark"]), d)
    db.add(
        SaPaperNav(
            account_id=account_id, trade_date=d,
            cash=_dec(cash), market_value=_dec(market_value),
            total_asset=_dec(total_asset), nav=_dec(nav, 6),
            daily_ret=_dec(total_asset / prev_total - 1.0, 8) if prev_total else None,
            benchmark_close=_dec(bench, 4) if bench is not None else None,
            drawdown=_dec(nav / peak - 1.0, 6),
        )
    )
    warn, fail = rules["drawdown"]
    alert_rows.append(
        _check_row(account_id, d, "drawdown", -(nav / peak - 1.0), warn, fail)
    )

    # --- ④ persist alerts + account cash
    db.add_all(alert_rows)
    account.cash = _dec(cash)
    db.commit()
    return {
        "status": "ok",
        "fills": fills,
        "signal": signal_msg,
        "alerts": len([r for r in alert_rows if r.status != "pass"]),
        "total_asset": round(total_asset, 2),
    }


def _qualifies_rebalance(d: date, dates_sorted: list[date], freq, today: date) -> bool:
    """Whether ``d`` is a signal date under ``freq``, live/replay consistent.

    Replay (future rows already in ``dates_sorted``) reproduces the backtest's
    "last trading day of week/month" exactly. Live (future rows absent) must
    not fire mid-period: a week/month signal only fires once the period has
    actually ended (``today`` past its final weekday/last day). N-day freq is
    prefix-stable and needs no such guard.
    """
    if d not in dates_sorted:
        return False
    try:
        n = int(freq)
    except (TypeError, ValueError):
        n = None
    if n is not None:
        return dates_sorted.index(d) % max(n, 1) == 0
    iso = d.isocalendar()
    if str(freq) == "W":
        week = [
            x for x in dates_sorted
            if (x.isocalendar().year, x.isocalendar().week) == (iso.year, iso.week)
        ]
        if d != max(week):
            return False
        # live guard: Friday must have arrived (or passed) for this to be the
        # week's real end; holiday weeks fire on their true last trading day
        friday = d + timedelta(days=4 - d.weekday())
        return today >= friday
    month = [x for x in dates_sorted if (x.year, x.month) == (d.year, d.month)]
    if d != max(month):
        return False
    if d.month == 12:
        period_end = date(d.year + 1, 1, 1) - timedelta(days=1)
    else:
        period_end = date(d.year, d.month + 1, 1) - timedelta(days=1)
    return today >= period_end


def _signal_targets(db: Session, cfg: dict, panel, d: date) -> list[str]:
    """Scope + score on one date via the backtest's own pass-1 function."""
    specs = pbs._resolve_specs(cfg.get("factors"), cfg.get("preset"))
    codes_all = factor_panel.select_universe(
        db, panel,
        date.fromisoformat(cfg["start"]), d,
        pool=str(cfg["pool"]), universe_size=cfg.get("liquidity_top_k"),
    )
    if not codes_all:
        return []
    from app.services.factor_panel import MarketPanel

    scoped = MarketPanel(
        close=panel.close.reindex(columns=codes_all),
        volume=panel.volume.reindex(columns=codes_all),
    )
    factor_codes = [s.code for s in specs]
    fp_values = {fc: factor_panel.panel_factor_values(scoped, fc) for fc in factor_codes}
    amt_panel = fp_values.get("amt20")
    if amt_panel is None:
        amt_panel = factor_panel.panel_factor_values(scoped, "amt20")
    listed = (
        factor_panel.pit_listed_mask(db, scoped, codes_all) if cfg["pool"] == "pit" else None
    )
    status = factor_panel.load_trade_status(db, scoped, codes_all)
    return pbs.scope_and_score_targets(
        db, t=pd.Timestamp(d), specs=specs, fp_values=fp_values, amt_panel=amt_panel,
        codes_all=codes_all, listed=listed, status=status,
        top_n=int(cfg["top_n"]), neutralize=str(cfg["neutralize"]),
        liquidity_top_k=cfg.get("liquidity_top_k"),
        exclude_st=bool(cfg["exclude_st"]), exclude_suspended=bool(cfg["exclude_suspended"]),
        only_tradable=bool(cfg["only_tradable"]),
    )


def _create_orders(
    db: Session, account_id: int, d: date,
    holdings: dict[str, int], targets: list[str], cfg: dict,
    last_price: dict[str, float], cash: float,
) -> int:
    """Membership diff vs holdings → T+1 orders; returns the order count.

    Buy shares are sized at fill time (backtest semantics); est_shares here is
    informational. rebalance_tol=None disables weight-based adjustment to keep
    paper/backtest drift-free by default.
    """
    exec_date = _next_weekday(d)
    tol = cfg.get("rebalance_tol")
    intents = paper_analytics.diff_orders(
        holdings, {c: 1.0 / len(targets) for c in targets},
        last_price, cash,
        rebalance_tol=float(tol) if tol else 1e9,
    )
    sell_intents = [i for i in intents if i.side == "sell"]
    buy_codes = {i.stock_code for i in intents if i.side == "buy"}
    intent_by_code = {i.stock_code: i for i in intents}
    # buys follow the target ranking (backtest iterates `for c in targets`) so
    # cash-constrained clipping picks the same names in the same order
    ordered = [(i, None) for i in sell_intents] + [
        (intent_by_code[c], rank) for rank, c in enumerate(targets) if c in buy_codes
    ]
    for it, _rank in ordered:
        db.add(
            SaPaperOrder(
                account_id=account_id, signal_date=d, exec_date=exec_date,
                stock_code=it.stock_code, side=it.side,
                target_weight=_dec(it.target_weight, 6),
                shares=it.est_shares if it.side == "sell" else None,
            )
        )
    return len(ordered)


def _closes_on(db: Session, codes: list[str], d: date) -> dict[str, float]:
    if not codes:
        return {}
    rows = db.execute(
        select(DailyPrice.stock_code, DailyPrice.close).where(
            DailyPrice.trade_date == d,
            DailyPrice.stock_code.in_(codes),
            DailyPrice.close.is_not(None),
        )
    ).all()
    return {c: float(x) for c, x in rows}


def _data_incomplete(db: Session, d: date) -> bool:
    """Same settled-row heuristic as the 23:00 sync retry (live days only)."""
    if d != date.today():
        return False
    from app.data.backfill import _COMPLETENESS_RATIO, settled_counts

    counts = settled_counts(db)
    today_count = counts.get(d, 0)
    prior = [c for dd, c in counts.items() if dd < d]
    if not prior:
        return False
    return today_count < _COMPLETENESS_RATIO * max(prior)


def _corporate_action_alerts(
    db: Session, account_id: int, d: date,
    holdings: dict[str, int], last_close: dict[str, float],
) -> list[SaDataQualityCheck]:
    """Ex-date consistency tripwire: qfq return must track the raw return."""
    if not holdings:
        return []
    prev_day = db.execute(
        select(DailyPrice.trade_date)
        .where(DailyPrice.trade_date < d)
        .order_by(DailyPrice.trade_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if prev_day is None:
        return []
    rows = db.execute(
        select(SaAdjustFactor.stock_code, SaAdjustFactor.trade_date, SaAdjustFactor.adj_factor)
        .where(
            SaAdjustFactor.stock_code.in_(list(holdings)),
            SaAdjustFactor.trade_date.in_([prev_day, d]),
        )
    ).all()
    factors: dict[str, dict[date, float]] = {}
    for code, td, f in rows:
        factors.setdefault(code, {})[td] = float(f)
    alerts: list[SaDataQualityCheck] = []
    for code, by_day in factors.items():
        if prev_day not in by_day or d not in by_day:
            continue
        ratio = by_day[d] / by_day[prev_day] if by_day[prev_day] else 1.0
        if abs(ratio - 1.0) < 1e-9:
            continue  # no ex-event
        raw = db.execute(
            select(SaKlineDaily.close)
            .where(SaKlineDaily.stock_code == code, SaKlineDaily.trade_date == d)
        ).scalar_one_or_none()
        raw_prev = db.execute(
            select(SaKlineDaily.close)
            .where(SaKlineDaily.stock_code == code, SaKlineDaily.trade_date == prev_day)
        ).scalar_one_or_none()
        if raw is None or raw_prev is None or last_close.get(code) is None:
            continue
        qfq_ret = last_close[code] / max(_prev_qfq_close(db, code, prev_day), 1e-9) - 1.0
        raw_ret = float(raw) / float(raw_prev) - 1.0
        if abs(qfq_ret - raw_ret) > 0.03:
            alerts.append(
                _check_row(account_id, d, "corporate_action", 1, Decimal("0"), Decimal("1"))
            )
            logger.warning(
                "paper account %s: %s qfq/raw return diverge on ex-date %s "
                "(qfq %.4f vs raw %.4f) — stale adjust data suspected",
                account_id, code, d, qfq_ret, raw_ret,
            )
    return alerts


def _prev_qfq_close(db: Session, code: str, prev_day: date) -> float:
    v = db.execute(
        select(DailyPrice.close).where(
            DailyPrice.stock_code == code, DailyPrice.trade_date == prev_day
        )
    ).scalar_one_or_none()
    return float(v) if v is not None else 0.0


# ---------------------------------------------------------------- alerts


def _ensure_rules(db: Session) -> dict[str, tuple[Decimal, Decimal]]:
    rows = db.execute(
        select(SaDataQualityRule).where(SaDataQualityRule.check_name == CHECK_NAME)
    ).scalars().all()
    if rows:
        return {r.metric_name: (r.warn_threshold, r.fail_threshold) for r in rows}
    for metric, warn, fail in DEFAULT_RULES:
        db.add(
            SaDataQualityRule(
                check_name=CHECK_NAME, metric_name=metric,
                warn_threshold=warn, fail_threshold=fail, enabled=1,
            )
        )
    db.commit()
    return {m: (w, f) for m, w, f in DEFAULT_RULES}


def _check_row(
    account_id: int, d: date, metric: str, value: float,
    warn: Decimal, fail: Decimal,
) -> SaDataQualityCheck:
    return SaDataQualityCheck(
        check_date=d,
        check_name=CHECK_NAME,
        metric_name=f"{metric}:{account_id}",
        metric_value=_dec(value, 4),
        status=paper_analytics.alert_eval(float(value), float(warn), float(fail)),
        detail={"account_id": account_id},
    )


# ---------------------------------------------------------------- read paths


def get_account(db: Session, account_id: int) -> dict:
    account = db.get(SaPaperAccount, account_id)
    if account is None:
        raise ValueError(f"账户不存在: {account_id}")
    latest_day = db.execute(
        select(SaPaperNav.trade_date)
        .where(SaPaperNav.account_id == account_id)
        .order_by(SaPaperNav.trade_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    positions: list[dict] = []
    total_mv = 0.0
    if latest_day is not None:
        rows = db.execute(
            select(SaPaperPosition)
            .where(
                SaPaperPosition.account_id == account_id,
                SaPaperPosition.trade_date == latest_day,
            )
        ).scalars().all()
        latest_nav = db.execute(
            select(SaPaperNav).where(
                SaPaperNav.account_id == account_id, SaPaperNav.trade_date == latest_day
            )
        ).scalar_one()
        total_mv = float(latest_nav.market_value)
        for r in rows:
            positions.append(
                {
                    "stock_code": r.stock_code,
                    "shares": int(r.shares),
                    "close_price": float(r.close_price),
                    "market_value": float(r.market_value),
                    "weight": float(r.market_value) / float(latest_nav.total_asset),
                }
            )
    alerts = db.execute(
        select(SaDataQualityCheck)
        .where(
            SaDataQualityCheck.check_name == CHECK_NAME,
            SaDataQualityCheck.metric_name.like(f"%:{account_id}"),
            SaDataQualityCheck.status != "pass",
        )
        .order_by(SaDataQualityCheck.check_date.desc())
        .limit(20)
    ).scalars().all()
    base = list_accounts(db)
    summary = next((a for a in base if a["id"] == account_id), {})
    return {
        "account": summary,
        "positions": positions,
        "market_value": round(total_mv, 2),
        "alerts": [
            {
                "metric": a.metric_name.split(":")[0],
                "status": a.status,
                "value": float(a.metric_value) if a.metric_value is not None else None,
                "check_date": a.check_date.isoformat(),
            }
            for a in alerts
        ],
    }


def nav_history(db: Session, account_id: int, start: date | None, end: date) -> dict:
    account = db.get(SaPaperAccount, account_id)
    if account is None:
        raise ValueError(f"账户不存在: {account_id}")
    cond = [SaPaperNav.account_id == account_id, SaPaperNav.trade_date <= end]
    if start is not None:
        cond.append(SaPaperNav.trade_date >= start)
    rows = db.execute(
        select(SaPaperNav).where(*cond).order_by(SaPaperNav.trade_date)
    ).scalars().all()
    navs = [float(r.nav) for r in rows]
    bench_raw = [float(r.benchmark_close) if r.benchmark_close is not None else None for r in rows]
    base_bench = next((b for b in bench_raw if b), None)
    bench_nav = [b / base_bench if (b is not None and base_bench) else None for b in bench_raw]
    cum_fees = float(
        db.execute(
            select(
                SaPaperTrade.commission + SaPaperTrade.stamp_duty + SaPaperTrade.transfer_fee
            ).where(SaPaperTrade.account_id == account_id)
        ).scalar()
        or 0.0
    )
    cash_w = [
        float(r.cash) / float(r.total_asset) if float(r.total_asset) else 0.0 for r in rows
    ]
    attribution = paper_analytics.attribution(
        navs or [1.0], bench_nav or [1.0], cum_fees, cash_w or [1.0],
        float(account.initial_cash),
    )
    return {
        "series": [
            {
                "trade_date": r.trade_date.isoformat(),
                "nav": float(r.nav),
                "daily_ret": float(r.daily_ret) if r.daily_ret is not None else None,
                "benchmark_nav": bn,
                "drawdown": float(r.drawdown) if r.drawdown is not None else None,
            }
            for r, bn in zip(rows, bench_nav, strict=True)
        ],
        "attribution": attribution,
    }


def orders_history(
    db: Session, account_id: int,
    start: date | None, end: date | None, status: str | None = None,
) -> dict:
    stmt = select(SaPaperOrder).where(SaPaperOrder.account_id == account_id)
    if start:
        stmt = stmt.where(SaPaperOrder.signal_date >= start)
    if end:
        stmt = stmt.where(SaPaperOrder.signal_date <= end)
    if status:
        stmt = stmt.where(SaPaperOrder.status == status)
    orders = db.execute(stmt.order_by(SaPaperOrder.signal_date.desc(), SaPaperOrder.id)).scalars().all()
    out = []
    for o in orders:
        trade = None
        if o.filled_trade_id:
            t = db.get(SaPaperTrade, o.filled_trade_id)
            if t is not None:
                trade = {
                    "price": float(t.price), "amount": float(t.amount),
                    "commission": float(t.commission),
                    "stamp_duty": float(t.stamp_duty),
                    "transfer_fee": float(t.transfer_fee),
                }
        out.append(
            {
                "signal_date": o.signal_date.isoformat(),
                "exec_date": o.exec_date.isoformat(),
                "stock_code": o.stock_code,
                "side": o.side,
                "shares": o.shares,
                "status": o.status,
                "fail_reason": o.fail_reason,
                "trade": trade,
            }
        )
    return {"orders": out}


def run_drift(db: Session, account_id: int, start: date, end: date) -> dict:
    """Replay the account config as a backtest and diff the two NAV paths."""
    account = db.get(SaPaperAccount, account_id)
    if account is None:
        raise ValueError(f"账户不存在: {account_id}")
    cfg = _cfg(account)
    payload = pbs.run_mf_backtest(
        db,
        factors=cfg.get("factors"),
        preset=cfg.get("preset"),
        start=start, end=end,
        freq=cfg["freq"], top_n=int(cfg["top_n"]),
        initial_cash=float(cfg["initial_cash"]),
        benchmark=str(cfg["benchmark"]), pool=str(cfg["pool"]),
        exclude_st=bool(cfg["exclude_st"]), exclude_suspended=bool(cfg["exclude_suspended"]),
        only_tradable=bool(cfg["only_tradable"]),
        neutralize=str(cfg["neutralize"]),
        liquidity_top_k=cfg.get("liquidity_top_k"),
        cost=cfg.get("cost"),
    )
    initial = float(cfg["initial_cash"])
    paper_rows = db.execute(
        select(SaPaperNav)
        .where(
            SaPaperNav.account_id == account_id,
            SaPaperNav.trade_date >= start,
            SaPaperNav.trade_date <= end,
        )
        .order_by(SaPaperNav.trade_date)
    ).scalars().all()
    paper = [(r.trade_date, float(r.nav)) for r in paper_rows]
    backtest = [
        (date.fromisoformat(p["date"]), p["value"] / initial) for p in payload["nav"]
    ]
    res = paper_analytics.drift_compare(paper, backtest)
    jaccard = []
    bt_holdings = {h["date"]: {x["code"] for x in h["holdings"]} for h in payload.get("holdings_timeline", [])}
    paper_pos: dict[str, set[str]] = {}
    for r in paper_rows:
        codes = db.execute(
            select(SaPaperPosition.stock_code).where(
                SaPaperPosition.account_id == account_id,
                SaPaperPosition.trade_date == r.trade_date,
            )
        ).scalars().all()
        paper_pos[r.trade_date.isoformat()] = set(codes)
    for d_str, bt_codes in bt_holdings.items():
        p_codes = paper_pos.get(d_str, set())
        jaccard.append(
            {"date": d_str, "value": round(paper_analytics.holding_jaccard(p_codes, bt_codes), 4)}
        )
    return {**res, "holding_jaccard": jaccard, "backtest_run_id": payload.get("run_id")}


def cleanup_account(db: Session, account_id: int) -> None:
    """Test helper: remove an account and all its rows (children first)."""
    db.query(SaDataQualityCheck).filter(
        SaDataQualityCheck.check_name == CHECK_NAME,
        SaDataQualityCheck.metric_name.like(f"%:{account_id}"),
    ).delete(synchronize_session=False)
    db.query(SaPaperNav).filter(SaPaperNav.account_id == account_id).delete(synchronize_session=False)
    db.query(SaPaperPosition).filter(SaPaperPosition.account_id == account_id).delete(synchronize_session=False)
    db.query(SaPaperTrade).filter(SaPaperTrade.account_id == account_id).delete(synchronize_session=False)
    db.query(SaPaperOrder).filter(SaPaperOrder.account_id == account_id).delete(synchronize_session=False)
    db.query(SaPaperAccount).filter(SaPaperAccount.id == account_id).delete(synchronize_session=False)
    db.commit()
