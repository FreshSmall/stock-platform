"""Board (打板) strategy (V2): limit-up board trading.

BUY when the stock hits/approaches the daily limit-up (pct >= threshold).
SELL the next day at open (intraday limit-up boards are hard to hold; this
approximates the classic "打板次日卖" pattern). Simplified: a stop-loss exits
if the price falls below the entry by ``stop_pct``.
"""

import backtrader as bt

from app.strategy.base import BaseStrategy


class BoardStrategy(BaseStrategy):
    """Limit-up board entry.

    Defaults: limit_threshold=9.5 (covers 10% main board), stop_pct=5.0.
    """

    params = (
        ("limit_threshold", 9.5),
        ("stop_pct", 5.0),
    )

    def __init__(self):
        super().__init__()
        self.prev_close = self.data.close(-1)
        self.entry_price = None

    def next(self):
        if len(self.data) < 2:
            return
        yesterday = self.prev_close[0]
        if yesterday in (0, None):
            return
        pct = (self.data.close[0] - yesterday) / yesterday * 100.0
        if self.position.size == 0:
            if pct >= self.params.limit_threshold:
                self.buy()
                self.entry_price = self.data.close[0]
        else:
            # exit next bar at open approximated by current close; or stop-loss
            drop = (
                (self.entry_price - self.data.close[0]) / self.entry_price * 100.0
                if self.entry_price
                else 0
            )
            if drop >= self.params.stop_pct:
                self.sell()
                self.entry_price = None
            elif pct < 0:
                # gap down next day → exit
                self.sell()
                self.entry_price = None
