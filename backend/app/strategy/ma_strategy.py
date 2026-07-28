"""MA (Simple Moving Average) crossover strategy.

BUY on golden cross (fast SMA crosses above slow SMA).
SELL on death cross (fast SMA crosses below slow SMA).
"""

import backtrader as bt

from app.strategy.base import BaseStrategy


class MaStrategy(BaseStrategy):
    """Golden/death cross on two simple moving averages.

    Default windows are 5 (fast) and 20 (slow) bars, matching the standard
    A-share short-term trend params. Override via Cerebro kwargs, e.g.
    ``cerebro.addstrategy(MaStrategy, fast=10, slow=30)``.
    """

    params = (
        ("fast", 5),
        ("slow", 20),
    )

    def __init__(self):
        super().__init__()
        self.sma_fast = bt.ind.SMA(self.data.close, period=self.params.fast)
        self.sma_slow = bt.ind.SMA(self.data.close, period=self.params.slow)
        self.crossover = bt.ind.CrossOver(self.sma_fast, self.sma_slow)

    def next(self):
        if self.position.size == 0:  # flat — look for an entry
            if self.crossover > 0:  # golden cross
                self.buy()
        else:  # in market — look for an exit
            if self.crossover < 0:  # death cross
                self.sell()
