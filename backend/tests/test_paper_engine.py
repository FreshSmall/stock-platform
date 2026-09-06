"""Paper trading engine tests (V3a / spec-006).

Real-data historical replay over a 2-month window, then a zero-drift
comparison against ``run_mf_backtest`` with the identical config — the
foundational guarantee that paper/backtest drift measures reality, not
implementation differences.
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import delete, select

from app.models.backtest import SaBacktestResult, SaBacktestRun
from app.models.paper import SaPaperNav, SaPaperOrder, SaPaperPosition, SaPaperTrade
from app.models.stock import DailyPrice
from app.services import paper_service
from app.services import portfolio_backtest_service as pbs

START, END = date(2026, 7, 1), date(2026, 8, 31)

# liquidity_top_k=None disables BOTH universe caps (select_universe and the
# per-date top-K) so replay and backtest scope identical candidate sets —
# the zero-drift precondition. With any cap the marginal membership depends
# on each path's amount-ranking window, an honest data-window drift source
# the drift page is designed to expose (not something to test away).
CONFIG = {
    "factors": [{"code": "amt20", "weight": 1.0, "direction": -1}],
    "top_n": 3,
    "freq": "10",
    "initial_cash": 100_000.0,
    "pool": "current",
    "liquidity_top_k": None,
    "only_tradable": True,
    "exclude_st": False,
    "exclude_suspended": False,
    "neutralize": "none",
    "benchmark": "sh000001",
    "start": START.isoformat(),
}


def _trading_days(db) -> list[date]:
    return list(
        db.execute(
            select(DailyPrice.trade_date)
            .where(DailyPrice.trade_date >= START, DailyPrice.trade_date <= END)
            .distinct()
            .order_by(DailyPrice.trade_date)
        ).scalars()
    )


def test_paper_replay_zero_drift(db_session):
    db = db_session
    account = paper_service.create_account(db, f"pytest-{uuid.uuid4().hex[:8]}", CONFIG)
    acc_id = account["id"]
    days = _trading_days(db)
    assert len(days) >= 40  # window sanity

    try:
        # ---- replay day by day
        for d in days:
            paper_service.paper_tick(db, acc_id, d)

        navs = db.execute(
            select(SaPaperNav)
            .where(SaPaperNav.account_id == acc_id)
            .order_by(SaPaperNav.trade_date)
        ).scalars().all()
        assert [n.trade_date for n in navs] == days

        # ---- accounting: total = cash + Σ position market values (per day)
        for n in navs:
            mv = db.execute(
                select(SaPaperPosition.market_value).where(
                    SaPaperPosition.account_id == acc_id,
                    SaPaperPosition.trade_date == n.trade_date,
                )
            ).scalars().all()
            assert float(n.total_asset) == pytest.approx(
                float(n.cash) + sum(float(x) for x in mv), abs=0.02
            )

        # ---- fills: next-day open ± slippage, fee schedule itemized
        trades = db.execute(select(SaPaperTrade).where(SaPaperTrade.account_id == acc_id)).scalars().all()
        assert trades, "replay produced no fills"
        for t in trades:
            op = db.execute(
                select(DailyPrice.open).where(
                    DailyPrice.stock_code == t.stock_code,
                    DailyPrice.trade_date == t.trade_date,
                )
            ).scalar_one()
            slip = 1.001 if t.side == "buy" else 0.999
            assert float(t.price) == pytest.approx(float(op) * slip, abs=5e-4)
            amount = float(t.amount)
            # fees are stored at 6dp — tolerance covers the storage rounding
            assert float(t.commission) == pytest.approx(max(amount * 2.5e-4, 5.0), rel=1e-6, abs=1e-6)
            assert float(t.transfer_fee) == pytest.approx(amount * 1e-5, rel=1e-6, abs=1e-6)
            if t.side == "sell":
                assert float(t.stamp_duty) == pytest.approx(amount * 5e-4, rel=1e-6, abs=1e-6)
            else:
                assert float(t.stamp_duty) == 0.0
            assert int(t.shares) % 100 == 0

        # ---- orders only on N-day rebalance dates, executed T+1
        orders = db.execute(select(SaPaperOrder).where(SaPaperOrder.account_id == acc_id)).scalars().all()
        signal_days = {o.signal_date for o in orders}
        reb_days = {days[i] for i in range(0, len(days), 10)}
        assert signal_days
        assert signal_days <= reb_days
        assert reb_days - signal_days <= {days[-1]}  # the final rebalance never executes
        for o in orders:
            assert o.exec_date > o.signal_date

        # ---- idempotency: re-tick three days → no new rows
        n_trades = len(trades)
        n_orders = len(orders)
        for d in (days[0], days[len(days) // 2], days[-1]):
            res = paper_service.paper_tick(db, acc_id, d)
            assert res["status"] == "already_done"
        assert (
            len(db.execute(select(SaPaperTrade).where(SaPaperTrade.account_id == acc_id)).scalars().all())
            == n_trades
        )
        assert (
            len(db.execute(select(SaPaperOrder).where(SaPaperOrder.account_id == acc_id)).scalars().all())
            == n_orders
        )

        # ---- zero drift vs the same-config portfolio backtest
        bt = pbs.run_mf_backtest(
            db,
            factors=CONFIG["factors"],
            start=START, end=END,
            freq="10", top_n=3, initial_cash=100_000.0,
            pool="current", only_tradable=True, liquidity_top_k=None,
            neutralize="none", exclude_st=False, exclude_suspended=False,
            user_id=None,
        )
        try:
            # the backtest drops the final rebalance (reb_dates[:-1]) while a
            # live paper account signals on it — compare up to the day before
            # that extra execution, which both paths experience identically
            final_reb = max(reb_days)
            extra_exec = min(
                (o.exec_date for o in orders if o.signal_date == final_reb),
                default=None,
            )
            assert len(bt["rebalances"]) == len(reb_days) - 1
            paper_nav = {n.trade_date.isoformat(): float(n.nav) for n in navs}
            bt_nav = {p["date"]: p["value"] / 100_000.0 for p in bt["nav"]}
            common = [
                d for d in sorted(set(paper_nav) & set(bt_nav))
                if extra_exec is None or date.fromisoformat(d) < extra_exec
            ]
            assert len(common) >= len(bt_nav) - 2
            for d_str in common:
                assert paper_nav[d_str] == pytest.approx(bt_nav[d_str], abs=5e-6), (
                    f"drift at {d_str}: paper {paper_nav[d_str]} vs backtest {bt_nav[d_str]}"
                )
        finally:
            db.execute(delete(SaBacktestResult).where(SaBacktestResult.run_id == bt["run_id"]))
            db.execute(delete(SaBacktestRun).where(SaBacktestRun.run_id == bt["run_id"]))
            db.commit()
    finally:
        paper_service.cleanup_account(db, acc_id)


def test_qualifies_rebalance_rules():
    f = paper_service._qualifies_rebalance
    week = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5), date(2026, 8, 6), date(2026, 8, 7)]
    # replay: only the week's last trading day qualifies
    assert f(date(2026, 8, 7), week, "W", date(2026, 9, 6)) is True
    assert f(date(2026, 8, 5), week, "W", date(2026, 9, 6)) is False
    # live: a mid-week day must not fire even though Friday has no data yet
    assert f(date(2026, 8, 5), week[:3], "W", date(2026, 8, 5)) is False
    assert f(date(2026, 8, 7), week, "W", date(2026, 8, 7)) is True
    # holiday-shortened week still fires on its true last day (replay)
    assert f(date(2026, 8, 6), week[:4], "W", date(2026, 9, 6)) is True
    # N-day is prefix-stable
    assert f(date(2026, 8, 3), week, "10", date(2026, 8, 3)) is True
    assert f(date(2026, 8, 7), week, "10", date(2026, 8, 3)) is False
    # month: fires only once the period has truly ended
    assert f(date(2026, 8, 31), [date(2026, 8, 28), date(2026, 8, 31)], "M", date(2026, 9, 6)) is True
    assert f(date(2026, 8, 28), [date(2026, 8, 28)], "M", date(2026, 8, 28)) is False


def test_account_status_flow(db_session):
    db = db_session
    account = paper_service.create_account(db, f"pytest-{uuid.uuid4().hex[:8]}", CONFIG)
    acc_id = account["id"]
    try:
        assert paper_service.set_status(db, acc_id, "pause")["status"] == "paused"
        days = _trading_days(db)[:3]
        for d in days:
            assert paper_service.paper_tick(db, acc_id, d)["status"] == "skipped"
        assert db.execute(
            select(SaPaperNav.id).where(SaPaperNav.account_id == acc_id).limit(1)
        ).first() is None
        assert paper_service.set_status(db, acc_id, "resume")["status"] == "active"
        with pytest.raises(ValueError, match="未知操作"):
            paper_service.set_status(db, acc_id, "explode")
    finally:
        paper_service.cleanup_account(db, acc_id)
