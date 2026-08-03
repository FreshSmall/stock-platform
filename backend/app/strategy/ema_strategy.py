"""EMA (Exponential Moving Average) crossover strategy (V2).

BUY on golden cross (fast EMA crosses above slow EMA).
SELL on death cross (fast EMA crosses below slow EMA).
"""

import backtrader as bt

from app.strategy.base import BaseStrategy


class EmaStrategy(BaseStrategy):
    """Golden/death cross on two exponential moving averages.

    Defaults: fast=12, slow=26 (standard MACD EMA pair).
    """

    params = (
        ("fast", 12),
        ("slow", 26),
    )

    def __init__(self):
        super().__init__()
        self.ema_fast = bt.ind.EMA(self.data.close, period=self.params.fast)
        self.ema_slow = bt.ind.EMA(self.data.close, period=self.params.slow)
        self.crossover = bt.ind.CrossOver(self.ema_fast, self.ema_slow)

    def next(self):
        if self.position.size == 0:
            if self.crossover > 0:
                self.buy()
        else:
            if self.crossover < 0:
                self.sell()
