"""Trend strategy (V2): ADX strength + MA multi-head alignment.

BUY when ADX > threshold (strong trend) AND fast MA > slow MA (bullish alignment).
SELL when ADX falls below threshold OR fast MA < slow MA (trend breaks).
"""

import backtrader as bt

from app.strategy.base import BaseStrategy


class TrendStrategy(BaseStrategy):
    """ADX-confirmed trend following.

    Defaults: adx_period=14, adx_threshold=25 (strong trend), fast=5, slow=20.
    """

    params = (
        ("adx_period", 14),
        ("adx_threshold", 25),
        ("fast", 5),
        ("slow", 20),
    )

    def __init__(self):
        super().__init__()
        self.adx = bt.ind.ADX(self.data, period=self.params.adx_period)
        self.sma_fast = bt.ind.SMA(self.data.close, period=self.params.fast)
        self.sma_slow = bt.ind.SMA(self.data.close, period=self.params.slow)

    def next(self):
        strong = self.adx[0] > self.params.adx_threshold
        bullish = self.sma_fast[0] > self.sma_slow[0]
        if self.position.size == 0:
            if strong and bullish:
                self.buy()
        else:
            if not strong or not bullish:
                self.sell()
