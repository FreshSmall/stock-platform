"""Tests for the A-share cost model + backtest rigor changes (V2.2 BP-V2.2-004).

Pure cost math + engine-level behaviour: asymmetric fees, the position sizer
(no more 1-share trades), and the per-date tradability guard.
"""

from datetime import date

import pytest

from app.services.cost_model import AShareCommission, CostParams


# --- cost math ---------------------------------------------------------------


def test_buy_cost_has_minimum_commission():
    cp = CostParams()
    # tiny ticket: minimum commission (5) + transfer fee dominates
    assert cp.buy_cost(1000.0) == pytest.approx(5.0 + 1000.0 * 0.00001)
    # large ticket: rate-based commission + transfer fee
    assert cp.buy_cost(1_000_000.0) == pytest.approx(
        1_000_000.0 * 0.00025 + 1_000_000.0 * 0.00001
    )


def test_sell_cost_adds_stamp_duty():
    cp = CostParams()
    amount = 1_000_000.0
    spread = cp.sell_cost(amount) - cp.buy_cost(amount)
    assert spread == pytest.approx(amount * cp.stamp_duty_rate)


def test_commission_info_asymmetric():
    ci = AShareCommission(commission=0.00025)
    # 10_000 shares @ 10 → amount 100_000
    buy = ci._getcommission(10_000, 10.0, pseudoexec=False)
    sell = ci._getcommission(-10_000, 10.0, pseudoexec=False)
    assert sell > buy
    assert sell - buy == pytest.approx(100_000.0 * 0.0005)
    # minimum commission on a small ticket
    small = ci._getcommission(10, 5.0, pseudoexec=False)  # 50 CNY ticket
    assert small == pytest.approx(5.0 + 50.0 * 0.00001)


# --- engine behaviour ---------------------------------------------------------


def test_trademap_fallback_inference(db_session):
    from app.services.backtest_service import _build_trademap

    class _Row:
        def __init__(self, d, pct, vol):
            self.trade_date, self.pct_change, self.volume = d, pct, vol

    rows = [
        _Row(date(2026, 6, 1), 5.0, 1_000_000),   # normal day
        _Row(date(2026, 6, 2), 10.0, 1_000_000),  # limit up → can't buy
        _Row(date(2026, 6, 3), -10.0, 1_000_000),  # limit down → can't sell
        _Row(date(2026, 6, 4), None, 0),           # no volume → suspended
    ]
    tm = _build_trademap(db_session, "ZZTESTCODE", rows)
    assert tm[date(2026, 6, 1)] == {"buy": True, "sell": True}
    assert tm[date(2026, 6, 2)] == {"buy": False, "sell": True}
    assert tm[date(2026, 6, 3)] == {"buy": True, "sell": False}
    assert tm[date(2026, 6, 4)] == {"buy": False, "sell": False}


def test_backtest_position_sizing_and_costs(db_session):
    """A run must trade ~full cash (not 1 share) and pay asymmetric fees."""
    from app.services.backtest_service import run_backtest

    out = run_backtest(
        db_session,
        strategy="ma",
        params={},
        stock_pool=["600519"],
        start_date=date(2026, 1, 1),
        end_date=date(2026, 9, 4),
    )
    trades = out.get("trades") or []
    assert trades, "expected at least one closed trade"
    # position model: entry size must be thousands of shares (98% of 100k
    # cash at ~1400 CNY/share ≈ 70 shares — NOT the old fixed 1 share)
    assert all(t["size"] > 1 for t in trades)
    # sanity: equity curve stays a plausible multiple of 100k initial cash
    eq = out["equity_curve"]
    assert all(50_000 <= v["equity"] <= 300_000 for v in eq)


def test_strategy_guard_blocks_buy_via_mini_backtest():
    """buy() is dropped on trademap-blocked days (real mini cerebro run)."""
    import backtrader as bt
    import pandas as pd

    from app.strategy.base import BaseStrategy

    class _BuyAndHold(BaseStrategy):
        def next(self):
            if not self.position:
                self.buy()

    def _run(trademap):
        idx = pd.bdate_range("2026-06-01", periods=6)
        df = pd.DataFrame(
            {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 1000.0},
            index=idx,
        )
        cerebro = bt.Cerebro(stdstats=False)
        cerebro.broker.setcash(100_000.0)
        cerebro.adddata(bt.feeds.PandasData(dataname=df))
        cerebro.addstrategy(_BuyAndHold)
        cerebro.trademap = trademap
        return cerebro.run()[0]

    # no map → position opens normally
    strat = _run({})
    assert strat.position.size > 0
    # every day buy-blocked (e.g. 一字涨停/停牌) → no position at all
    all_blocked = {
        d.date(): {"buy": False, "sell": False}
        for d in pd.bdate_range("2026-06-01", periods=6)
    }
    strat2 = _run(all_blocked)
    assert strat2.position.size == 0
