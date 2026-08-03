"""Leader (龙头) strategy (V2): strong gain + high volume.

A simplified single-stock version of the board-leader pattern: BUY when the
stock surges (pct change > threshold) on heavy volume (vol ratio > threshold),
SELL on a trailing stop or volume dry-up. Without cross-stock ranking inside a
single backtrader feed this approximates "today this stock looked like a leader".
"""

import backtrader as bt

from app.strategy.base import BaseStrategy


class LeaderStrategy(BaseStrategy):
    """Volume-confirmed momentum leader.

    Defaults: gain_threshold=5.0 (%), vol_period=5, vol_ratio=1.5, ma_period=10.
    """

    params = (
        ("gain_threshold", 5.0),
        ("vol_period", 5),
        ("vol_ratio", 1.5),
        ("ma_period", 10),
    )

    def __init__(self):
        super().__init__()
        self.avg_vol = bt.ind.SMA(self.data.volume, period=self.params.vol_period)
        self.ma = bt.ind.SMA(self.data.close, period=self.params.ma_period)
        # today's pct change requires yesterday's close
        self.prev_close = self.data.close(-1)

    def next(self):
        if len(self.data) < 2:
            return
        yesterday = self.prev_close[0]
        if yesterday in (0, None):
            return
        pct = (self.data.close[0] - yesterday) / yesterday * 100.0
        vol_ok = (
            self.avg_vol[0] > 0
            and self.data.volume[0] / self.avg_vol[0] > self.params.vol_ratio
        )
        if self.position.size == 0:
            if pct > self.params.gain_threshold and vol_ok:
                self.buy()
        else:
            # exit on close below MA (trend break)
            if self.data.close[0] < self.ma[0]:
                self.sell()
