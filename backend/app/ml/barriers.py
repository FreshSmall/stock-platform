"""Triple-barrier labelling (López de Prado, AFML ch.3) on A-share daily bars.

A signal confirmed at ``t0``'s close is filled at ``t0+1``'s open (T+1), then:

- upper barrier  ``entry * (1 + pt)``  → label 1 (true breakout)
- lower barrier  ``entry * (1 - sl)``  → label 0 (false breakout)
- vertical barrier ``t0 + horizon`` trading days → label = sign of exit return

A bar touching both barriers on the same day resolves as a stop (conservative:
underestimate rather than overestimate the win rate). Every net return deducts
a round-trip ``COST``. Samples whose entry would gap open near the daily limit
are unfillable in practice and are dropped (``None``).
"""

import pandas as pd

COST = 0.0015  # round-trip commission + stamp duty + slippage, ≈0.15%


def triple_barrier(
    df: pd.DataFrame,
    t0: int,
    pt: float = 0.04,
    sl: float = 0.02,
    horizon: int = 10,
    max_entry_gap: float = 0.095,
    atr_pct: float | None = None,
    up_mult: float = 2.0,
    dn_mult: float = 1.0,
) -> dict | None:
    """Label the signal fired at ``t0``; ``None`` when the sample is unusable.

    Requires ``open`` / ``high`` / ``low`` / ``close`` columns. Returns the
    label (1/0), net return after costs, exit day ``t_end``, holding days and
    entry/exit prices for inspection.

    Barrier sizing: fixed ``+pt``/``-sl`` fractions by default. Pass the
    signal-day ATR ratio (ATR14/close) via ``atr_pct`` for volatility-scaled
    barriers — upper = ``entry * (1 + up_mult * atr_pct)``, lower =
    ``entry * (1 - dn_mult * atr_pct)``. A 3%-ATR small cap gets wider stops
    than a 1%-ATR mega cap, so labels mean the same thing ("one good/bad
    volatility unit") across the universe instead of one size fitting none.
    """
    entry_i = t0 + 1
    if entry_i >= len(df):
        return None
    entry = float(df["open"].iloc[entry_i])
    ref_close = float(df["close"].iloc[t0])
    if entry <= 0 or ref_close <= 0:
        return None
    if entry / ref_close - 1.0 > max_entry_gap:
        return None  # gap close to limit-up: not fillable in practice

    if atr_pct is not None and atr_pct > 0:
        up, dn = entry * (1.0 + up_mult * atr_pct), entry * (1.0 - dn_mult * atr_pct)
    else:
        up, dn = entry * (1.0 + pt), entry * (1.0 - sl)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)

    t_end = min(t0 + horizon, len(df) - 1)
    label, exit_px = 0, None
    for t in range(entry_i, t_end + 1):
        if low[t] <= dn:  # conservative: same-day double touch resolves as stop
            label, exit_px, t_end = 0, dn, t
            break
        if high[t] >= up:
            label, exit_px, t_end = 1, up, t
            break
    if exit_px is None:  # vertical barrier: label by exit-return sign
        exit_px = close[t_end]
        label = int(exit_px > entry)

    return {
        "t0": t0,
        "t1": entry_i,
        "t_end": t_end,
        "label": label,
        "entry": entry,
        "exit": exit_px,
        "ret": exit_px / entry - 1.0 - COST,
        "hold": t_end - entry_i,
    }
