"""Tests for the MACD crossover strategy and its registry entry.

All strategy tests run on SYNTHETIC pandas data via a backtrader Cerebro —
no database. The data is shaped (down → up → down → up → down → up) so
that the MACD line (dif) actually crosses its signal line (dea) several
times: a backtrader ``CrossOver`` only emits a signal on the bar where the
two lines *cross*, so a steady slope never triggers — we need the slope to
flip repeatedly (the C1 lesson).
"""

import backtrader as bt
import pandas as pd
import pytest

from app.strategy import registry
from app.strategy.macd_strategy import MacdStrategy


def _crossover_cerebro(df, cls=MacdStrategy, cash=100000.0, **kwargs):
    """Wire up a Cerebro with synthetic data and the MACD strategy."""
    cerebro = bt.Cerebro()
    cerebro.broker.setcash(cash)
    cerebro.broker.setcommission(commission=0.0003)
    cerebro.adddata(bt.feeds.PandasData(dataname=df))
    cerebro.addstrategy(cls, **kwargs)
    return cerebro


def _oscillating_data(n=120, start=100.0):
    """A down→up→down→up→down→up series so MACD crosses signal repeatedly.

    The six 20-bar slope segments (−1, +2, −2, +1.5, −1, +2) make the MACD
    line overtake and fall back through its signal line multiple times,
    yielding at least one golden cross + one death cross within 120 bars.
    The Series shares the DataFrame's DatetimeIndex so the constructor
    does not reindex (and NaN-out) the values (C1 lesson).
    """
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    prices = []
    p = start
    slopes = [-1] * 20 + [2] * 20 + [-2] * 20 + [1.5] * 20 + [-1] * 20 + [2] * 20
    for s in slopes[:n]:
        p += s
        prices.append(p)
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


# --- Strategy behaviour ----------------------------------------------------


def test_macd_strategy_runs_and_trades():
    """The oscillating data must produce at least one closed/open trade."""
    df = _oscillating_data()
    cerebro = _crossover_cerebro(df)
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    results = cerebro.run()
    ta = results[0].analyzers.trades.get_analysis()
    total = ta.get("total", {}) or {}
    total_closed = total.get("closed", 0) or 0
    total_open = total.get("open", 0) or 0
    # With 2 golden + 2 death crosses there should be real trade activity.
    assert (total_closed + total_open) > 0
    # Portfolio value must be a positive, sane number.
    assert cerebro.broker.getvalue() > 0


def test_macd_strategy_golden_cross_enters_position():
    """After a golden cross the strategy must actually go long."""
    df = _oscillating_data()
    transitions = []

    class _Tracing(MacdStrategy):
        def next(self):
            prev_pos = getattr(self, "_prev_pos", 0)
            cur_pos = self.position.size
            if prev_pos == 0 and cur_pos > 0:
                transitions.append("BUY")
            elif prev_pos > 0 and cur_pos == 0:
                transitions.append("SELL")
            self._prev_pos = cur_pos
            super().next()

    cerebro = _crossover_cerebro(df, cls=_Tracing)
    cerebro.run()
    # The oscillating data yields at least one golden cross → at least one BUY.
    assert "BUY" in transitions
    assert "SELL" in transitions  # and a death cross/divergence exit too
    # Broker sanity after the run.
    assert cerebro.broker.getvalue() > 0


# --- Registry --------------------------------------------------------------


def test_registry_has_macd():
    meta = registry.get("macd")
    assert meta is not None
    assert meta.available is True
    assert meta.cls is MacdStrategy
    names = {p["name"] for p in meta.params}
    assert names == {"period_me1", "period_me2", "period_signal"}


def test_available_strategies_now_has_ma_and_macd():
    avail = {m.name for m in registry.available_strategies()}
    assert {"ma", "macd"} <= avail


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
