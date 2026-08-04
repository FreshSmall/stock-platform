"""Low-buy (低吸) strategy (V2): pullback to MA support + shrinking volume.

BUY when price pulls back near the MA (within ``pullback_pct`` below MA) AND
volume shrinks (vol ratio < threshold, indicating selling exhaustion).
SELL when price rises above MA by ``profit_pct`` or breaks below by stop_pct.
"""

import backtrader as bt

from app.strategy.base import BaseStrategy


class LowBuyStrategy(BaseStrategy):
    """Pullback-to-support mean-reversion entry.

    Defaults: ma_period=20, pullback_pct=3.0 (price within 3% below MA),
    vol_period=5, vol_ratio=0.8 (shrinking), profit_pct=8.0, stop_pct=5.0.
    """

    params = (
        ("ma_period", 20),
        ("pullback_pct", 3.0),
        ("vol_period", 5),
        ("vol_ratio", 0.8),
        ("profit_pct", 8.0),
        ("stop_pct", 5.0),
    )

    def __init__(self):
        super().__init__()
        self.ma = bt.ind.SMA(self.data.close, period=self.params.ma_period)
        self.avg_vol = bt.ind.SMA(self.data.volume, period=self.params.vol_period)
        self.entry_price = None

    def next(self):
        close = self.data.close[0]
        ma = self.ma[0]
        if ma in (0, None) or self.avg_vol[0] in (0, None):
            return
        dev = (ma - close) / ma * 100.0  # positive when price below MA
        vol_shrink = self.data.volume[0] / self.avg_vol[0] < self.params.vol_ratio
        if self.position.size == 0:
            if 0 < dev <= self.params.pullback_pct and vol_shrink:
                self.buy()
                self.entry_price = close
        else:
            gain = (close - self.entry_price) / self.entry_price * 100.0
            loss = (self.entry_price - close) / self.entry_price * 100.0
            if gain >= self.params.profit_pct or loss >= self.params.stop_pct:
                self.sell()
                self.entry_price = None
