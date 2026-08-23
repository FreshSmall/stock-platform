"""Performance summary and raw-vs-filtered comparison for trade lists."""

import numpy as np
import pandas as pd


def perf(trades: pd.DataFrame) -> dict:
    """Headline stats for a trade list (rows: ``ret`` / ``hold`` / ``trade_date``).

    Trades are sequenced by signal date and compounded; ``profit_factor`` is
    gross win / gross loss (``None`` when no losing trade — not ``inf`` JSON).
    """
    if trades is None or trades.empty:
        return {"n_trades": 0}
    trades = trades.sort_values("trade_date")
    rets = trades["ret"].astype(float)
    gross_win = float(rets[rets > 0].sum())
    gross_loss = float(-rets[rets <= 0].sum())
    eq = (1.0 + rets).cumprod()
    return {
        "n_trades": int(len(trades)),
        "win_rate": round(float((rets > 0).mean()), 4),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "avg_ret": round(float(rets.mean()), 5),
        "total_ret": round(float(eq.iloc[-1] - 1.0), 4),
        "max_drawdown": round(float((eq / eq.cummax() - 1.0).min()), 4),
        "avg_hold_days": round(float(trades["hold"].mean()), 2),
    }


def equity_curve(trades: pd.DataFrame, points: int = 200) -> list[float]:
    """Compounded net-value curve at trade granularity, downsampled to ``points``."""
    if trades is None or trades.empty:
        return []
    eq = (1.0 + trades.sort_values("trade_date")["ret"].astype(float)).cumprod()
    if len(eq) > points:
        eq = eq.iloc[np.linspace(0, len(eq) - 1, points).astype(int)]
    return [round(float(v), 5) for v in eq]
