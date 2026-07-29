"""Tests for the MA crossover strategy and the strategy registry.

All strategy tests run on SYNTHETIC pandas data via a backtrader Cerebro —
no database. The data is shaped (down -> up -> down -> up) so that the fast
SMA actually crosses the slow SMA in both directions: a backtrader
``CrossOver`` only emits a signal on the bar where the two lines *cross*,
so we need the slope to flip rather than just a steady trend.
"""

import backtrader as bt
import pandas as pd
import pytest

from app.strategy import registry
from app.strategy.ma_strategy import MaStrategy


def _make_cerebro(df, strategy_cls, cash=100000.0, **strategy_kwargs):
    """Wire up a Cerebro with synthetic data and the given strategy."""
    cerebro = bt.Cerebro()
    cerebro.broker.setcash(cash)
    cerebro.broker.setcommission(commission=0.0003)
    cerebro.adddata(bt.feeds.PandasData(dataname=df))
    cerebro.addstrategy(strategy_cls, **strategy_kwargs)
    return cerebro


def _synthetic_cross_data(n=80, start=100.0):
    """A down -> up -> down -> up series guaranteed to cross in both dirs.

    The four-segment slope pattern (−1, +2, −2, +1 over 20 bars each) makes
    the 5-period SMA overtake and then fall back through the 20-period SMA
    twice, so we get one golden cross + one death cross + one more golden
    cross within 80 bars.
    """
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    prices = []
    p = start
    for i in range(n):
        if i < 20:
            p -= 1.0  # downtrend → fast below slow
        elif i < 40:
            p += 2.0  # strong uptrend → golden cross
        elif i < 60:
            p -= 2.0  # downtrend → death cross
        else:
            p += 1.0  # recovery → second golden cross
        prices.append(p)
    # Use a Series *with* the same DatetimeIndex so that the DataFrame
    # constructor does not reindex (and NaN-out) the values when an explicit
    # `index=` is passed alongside Series inputs.
    close = pd.Series(prices, dtype=float, index=dates)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": [1_000_000] * n,
        },
        index=dates,
    )


def _synthetic_strong_uptrend(n=40):
    """A series that ends with a strong uptrend so MA5 crosses above MA20.

    ``backtrader``'s ``CrossOver`` only fires when the fast line *crosses
    through* the slow line; a purely monotonic rise never triggers a signal
    because MA5 is above MA20 from the very first valid bar. We therefore
    prepend a short decline to push MA5 below MA20, then a steep rise to
    produce a real golden cross. The strategy finishes holding a position.
    """
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    prices = []
    p = 100.0
    for i in range(n):
        if i < 15:
            p -= 1.0  # initial decline → MA5 below MA20
        else:
            p += 2.0  # strong rise → golden cross, ends holding
        prices.append(p)
    close = pd.Series(prices, dtype=float, index=dates)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": [1_000_000] * n,
        },
        index=dates,
    )


# --- Strategy behaviour ----------------------------------------------------


def test_ma_strategy_executes_and_produces_trades():
    df = _synthetic_cross_data()
    cerebro = _make_cerebro(df, MaStrategy, fast=5, slow=20)
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    results = cerebro.run()
    strat = results[0]

    total = strat.analyzers.trades.get_analysis().get("total", {})
    # The shape produces at least one closed trade plus the final open trade.
    assert (total.get("closed", 0) + total.get("open", 0)) > 0
    # Portfolio value must be a positive, sane number.
    assert cerebro.broker.getvalue() > 0


def test_ma_strategy_golden_cross_buys():
    """A strongly uptrending series should leave us holding a position."""
    df = _synthetic_strong_uptrend()
    cerebro = _make_cerebro(df, MaStrategy, fast=5, slow=20)
    results = cerebro.run()
    strat = results[0]
    # The uptrend pushes MA5 above MA20 → golden cross → strategy buys.
    assert strat.position.size > 0
    # Buying spends cash, so cash must drop below the starting 100k.
    assert cerebro.broker.getcash() < 100_000.0


def test_ma_strategy_sells_on_death_cross():
    """After both a golden and a death cross, the position should be closed."""
    df = _synthetic_cross_data()
    cerebro = _make_cerebro(df, MaStrategy, fast=5, slow=20)
    results = cerebro.run()
    strat = results[0]

    total = strat.analyzers  # noqa: F841 (placeholder for clarity)
    # Track position transitions: there must have been a buy and a sell.
    # We re-run with a tracing subclass to capture entry/exit.
    transitions = []

    class _Tracing(MaStrategy):
        def next(self):
            prev_pos = getattr(self, "_prev_pos", 0)
            cur_pos = self.position.size
            if (prev_pos == 0) and (cur_pos > 0):
                transitions.append("BUY")
            elif (prev_pos > 0) and (cur_pos == 0):
                transitions.append("SELL")
            self._prev_pos = cur_pos
            super().next()

    cerebro2 = bt.Cerebro()
    cerebro2.broker.setcash(100_000.0)
    cerebro2.broker.setcommission(commission=0.0003)
    cerebro2.adddata(bt.feeds.PandasData(dataname=df))
    cerebro2.addstrategy(_Tracing, fast=5, slow=20)
    cerebro2.run()
    assert "BUY" in transitions
    assert "SELL" in transitions


# --- Registry --------------------------------------------------------------


def test_registry_has_ma_and_v2_placeholders():
    names = {m.name for m in registry.all_strategies()}
    assert "ma" in names
    assert "macd" in names  # added in C2
    # V2 unavailable placeholders
    v2 = {m.name for m in registry.all_strategies() if not m.available}
    assert {"ema", "trend", "leader", "board", "lowbuy", "breakout"} <= v2


def test_registry_ma_metadata():
    meta = registry.get("ma")
    assert meta is not None
    assert meta.available is True
    assert meta.cls is MaStrategy
    param_names = {p["name"] for p in meta.params}
    assert param_names == {"fast", "slow"}


def test_available_strategies_excludes_v2():
    avail = {m.name for m in registry.available_strategies()}
    assert "ma" in avail
    assert "ema" not in avail


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
