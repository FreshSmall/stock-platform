"""Primary model: ascending-trendline breakout signals (no lookahead).

The trendline at bar ``t`` is the line through the two most recent pivot lows
that were *confirmed* by ``t-1``'s close: a pivot low at ``i`` needs ``order``
higher lows on each side, so it only becomes observable at ``i + order``. The
signal fires when today's close crosses above the extrapolated line while
yesterday's close sat at or below it — a first cross (lag 1), so consecutive
bars above the line don't re-fire.
"""

from collections import deque

import pandas as pd


def pivot_lows(low: pd.Series, order: int = 3) -> list[tuple[int, float]]:
    """Index/price pairs of pivot lows, as observable at the END of the series.

    ``i`` is a pivot low when ``low[i]`` is the unique minimum of the window
    ``[i-order, i+order]``. Callers must respect the confirmation lag: the
    pivot is only *known* at bar ``i + order`` (see :func:`trendline_signals`).
    """
    vals = low.to_numpy(dtype=float)
    out: list[tuple[int, float]] = []
    for i in range(order, len(vals) - order):
        window = vals[i - order : i + order + 1]
        if vals[i] == window.min() and (window == vals[i]).sum() == 1:
            out.append((i, float(vals[i])))
    return out


def trendline_signals(df: pd.DataFrame, order: int = 3) -> pd.DataFrame:
    """Breakout signals: today's close crosses above the ascending trendline.

    Requires columns ``low`` / ``close``; attaches ``trade_date`` when the
    frame carries one. Returns one row per signal with the positional index
    ``t``, the two pivot indices ``i1`` / ``i2``, the line ``slope`` and value
    at ``t``, and the breakout magnitude ``dev`` (close vs line). Empty frame
    when no signal fires.
    """
    close = df["close"].to_numpy(dtype=float)
    pivots = pivot_lows(df["low"], order)
    has_dates = "trade_date" in df.columns

    rows: list[dict] = []
    last2: deque[tuple[int, float]] = deque(maxlen=2)
    j = 0
    for t in range(1, len(df)):
        # roll the confirmable-pivot window forward: at t we may use pivots
        # confirmed by t-1's close, i.e. i + order <= t - 1.
        while j < len(pivots) and pivots[j][0] + order <= t - 1:
            last2.append(pivots[j])
            j += 1
        if len(last2) < 2:
            continue
        (i1, p1), (i2, p2) = last2
        slope = (p2 - p1) / (i2 - i1)
        if slope <= 0:  # ascending trendlines only
            continue
        line_t = p2 + slope * (t - i2)
        line_t1 = p2 + slope * (t - 1 - i2)
        # first cross: yesterday at/below yesterday's line, today above today's
        # (the stronger check keeps ``dev`` strictly positive)
        if close[t - 1] <= line_t1 and close[t] > line_t:
            row = {
                "t": t,
                "i1": i1,
                "i2": i2,
                "slope": slope,
                "line": line_t,
                "dev": close[t] / line_t - 1.0,
            }
            if has_dates:
                row["trade_date"] = df["trade_date"].iloc[t]
            rows.append(row)
    return pd.DataFrame(rows)
