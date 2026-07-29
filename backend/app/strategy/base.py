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

    def next(self):
        raise NotImplementedError
