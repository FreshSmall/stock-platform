"""MACD crossover strategy with simple bearish-divergence guard.

BUY on a golden cross (the MACD line, ``dif``, crosses above its signal
line, ``dea``). SELL on a death cross (``dif`` crosses below ``dea``). A
naive top-divergence check (price prints a 20-bar high while the MACD line
prints a lower high than its own 20-bar high) acts as an early exit, so we
trim before the death cross is confirmed.

Uses backtrader's built-in :class:`bt.ind.MACD`, which exposes two lines:
``macd`` (the dif line) and ``signal`` (the dea line). A ``CrossOver``
between them yields the golden/death-cross events.
"""

import backtrader as bt

from app.strategy.base import BaseStrategy


class MacdStrategy(BaseStrategy):
    """Golden/death cross on MACD dif vs dea, plus a divergence guard.

    Default periods (12/26/9) are the canonical MACD parameters. Override
    via Cerebro kwargs, e.g. ``cerebro.addstrategy(MacdStrategy, period_me1=10,
    period_me2=20, period_signal=5)``.
    """

    params = (
        ("period_me1", 12),
        ("period_me2", 26),
        ("period_signal", 9),
    )

    def __init__(self):
        super().__init__()
        # bt.ind.MACD exposes `.macd` (dif) and `.signal` (dea) lines.
        self.macd = bt.ind.MACD(
            self.data.close,
            period_me1=self.params.period_me1,
            period_me2=self.params.period_me2,
            period_signal=self.params.period_signal,
        )
        # Golden/death cross: dif (macd.macd) over/under dea (macd.signal).
        self.crossover = bt.ind.CrossOver(self.macd.macd, self.macd.signal)
        # Rolling 20-bar highs feed the naive bearish-divergence guard.
        self.price_high = bt.ind.Highest(self.data.high, period=20)
        self.macd_high = bt.ind.Highest(self.macd.macd, period=20)

    def next(self):
        in_market = self.position.size != 0
        if not in_market:  # flat — look for an entry
            if self.crossover > 0:  # golden cross → enter
                self.buy()
        else:  # in market — look for an exit
            # Bearish divergence: price prints a 20-bar high while the MACD
            # line fails to match its own 20-bar high (momentum lagging price).
            price_new_high = self.data.high[0] >= self.price_high[-1]
            macd_lower_high = self.macd.macd[0] < self.macd_high[-1]
            if self.crossover < 0 or (price_new_high and macd_lower_high):
                self.sell()
