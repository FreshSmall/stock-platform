"""Breakout (突破) strategy (V2): Bollinger upper-band breakout + volume.

BUY when close breaks above the BOLL upper band on heavy volume.
SELL on a trailing stop (close back below the mid band) or stop-loss.
"""

import backtrader as bt

from app.strategy.base import BaseStrategy


class BreakoutStrategy(BaseStrategy):
    """Bollinger-band breakout with volume confirmation.

    Defaults: boll_period=20, boll_dev=2.0, vol_period=5, vol_ratio=1.5,
    stop_pct=5.0.
    """

    params = (
        ("boll_period", 20),
        ("boll_dev", 2.0),
        ("vol_period", 5),
        ("vol_ratio", 1.5),
        ("stop_pct", 5.0),
    )

    def __init__(self):
        super().__init__()
        self.boll = bt.ind.BollingerBands(
            self.data.close,
            period=self.params.boll_period,
            devfactor=self.params.boll_dev,
        )
        self.avg_vol = bt.ind.SMA(self.data.volume, period=self.params.vol_period)
        self.entry_price = None

    def next(self):
        close = self.data.close[0]
        upper = self.boll.lines.top[0]
        mid = self.boll.lines.mid[0]
        if upper in (0, None) or self.avg_vol[0] in (0, None):
            return
        vol_ok = self.data.volume[0] / self.avg_vol[0] > self.params.vol_ratio
        if self.position.size == 0:
            if close > upper and vol_ok:
                self.buy()
                self.entry_price = close
        else:
            loss = (
                (self.entry_price - close) / self.entry_price * 100.0
                if self.entry_price
                else 0
            )
            if close < mid or loss >= self.params.stop_pct:
                self.sell()
                self.entry_price = None
