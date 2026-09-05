"""Strategy base classes built on backtrader.

Subclasses set ``params`` and implement signal logic in ``next()``. The
registry (C2+) maps strategy names to these classes.
"""

import backtrader as bt


class BaseStrategy(bt.Strategy):
    """Common base for all platform strategies.

    Subclasses define a ``params`` tuple/dict and a ``next()`` that issues
    buy/sell orders based on indicator crossovers. Logging via
    :py:meth:`log`, which prefixes each line with the bar's ISO date.
    """

    params = ()

    def log(self, txt: str, *args) -> None:
        """Print ``txt`` with the current bar's date prefix.

        Supports ``str.format``-style interpolation when positional args are
        supplied, e.g. ``self.log("BUY @ {:.2f}", order.executed.price)``.
        """
        dt = self.datas[0].datetime.date(0)
        print(f"{dt.isoformat()} | {txt.format(*args) if args else txt}")

    def __init__(self):
        # subclasses call super().__init__() after setting their indicators
        pass

    def _tradable(self, side: str) -> bool:
        """V2.2 T2.2: per-date tradability check (suspension / limit boards).

        The engine attaches ``cerebro.trademap`` ({date: {"buy","sell"}});
        missing map or missing date defaults to tradable (no restriction
        known). Subclasses never call this directly — buy()/sell() guard it.
        """
        tm = getattr(self.cerebro, "trademap", None)
        if not tm:
            return True
        info = tm.get(self.datas[0].datetime.date(0))
        if not info:
            return True
        return bool(info.get(side, True))

    def buy(self, *args, **kwargs):
        """Order a BUY unless today is untradable (suspension / limit-up).

        Returning None (no order) on a blocked day models "couldn't get
        filled" — the signal is consumed, exactly as in live trading.
        """
        if not self._tradable("buy"):
            return None
        return super().buy(*args, **kwargs)

    def sell(self, *args, **kwargs):
        """Order a SELL unless today is untradable (suspension / limit-down).
        Positions carried through blocked days resume selling when the
        restriction lifts (or the strategy's next exit signal fires).
        """
        if not self._tradable("sell"):
            return None
        return super().sell(*args, **kwargs)

    def next(self):
        raise NotImplementedError
