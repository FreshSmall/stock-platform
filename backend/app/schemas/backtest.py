"""Request/response schemas for the backtest API (Task C3).

These mirror the DB models in :mod:`app.models.backtest` but stay loose on the
``params`` / ``equity_curve`` / ``trades`` shapes: those are strategy-defined
JSON, so we type them as ``dict`` / ``list[dict]`` rather than locking the
front end to a fixed contract.
"""

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel


class BacktestRequest(BaseModel):
    """Submit a new backtest run.

    ``strategy`` is a registry key (``ma`` / ``macd``); ``params`` are passed
    straight to the backtrader Strategy class as kwargs; ``stock_pool`` holds
    the codes to trade — V1 only acts on the FIRST code (single-stock focus).
    """

    strategy: str  # "ma" | "macd"
    params: dict[str, Any] = {}  # strategy params, e.g. {"fast":5,"slow":20}
    stock_pool: list[str]  # stock codes; V1 typically 1 code
    start_date: date
    end_date: date
    initial_cash: Decimal = Decimal("100000")
    commission: Decimal = Decimal("0.0003")
    slippage: Decimal = Decimal("0.0001")

    # V2.1 sample governance (spec-004 BP-V2.1-005): flags gate the TARGET's
    # eligibility at the backtest window's first bar (matching-layer
    # constraints — per-day buy/sell blocking — land with V2.2 T2.2's
    # execution-model rework). Defaults keep the exact V2 behaviour.
    exclude_st: bool = False
    exclude_suspended: bool = False
    only_tradable: bool = False


class BacktestResultMetrics(BaseModel):
    """The 5 headline metrics surfaced in the status payload."""

    return_rate: Optional[Decimal] = None  # total return %
    max_drawdown: Optional[Decimal] = None  # %
    sharpe: Optional[Decimal] = None
    win_rate: Optional[Decimal] = None  # %
    trade_count: int = 0
    final_value: Optional[Decimal] = None


class BacktestResult(BaseModel):
    """Full result envelope returned to the client (run status + metrics)."""

    run_id: str
    status: str
    metrics: Optional[BacktestResultMetrics] = None
    equity_curve: Optional[list[dict]] = None  # [{date, equity}, ...]
    trades: Optional[list[dict]] = None  # [{date, side, price, size, pnl}, ...]
    error: Optional[str] = None
