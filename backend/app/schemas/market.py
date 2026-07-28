"""Pydantic v2 response schemas for the market overview API (Task B3).

These models shape the ``data`` field of the unified response envelope (see
:mod:`app.schemas.common`) for the three market-overview endpoints: index
quotes, market breadth summary, and the hot-stocks leaderboard.

Most numeric fields are ``Optional`` because the underlying tables legitimately
contain NULLs (and, in the case of indices, the data is not yet populated —
see :func:`app.services.market_service.get_indices`).
"""

from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class IndexQuote(BaseModel):
    """One major A-share index (上证指数 / 深证成指 / 创业板指).

    ``close``/``pct_change`` are ``None`` until AkShare index ingestion lands in
    Task B4 — the ``daily_prices`` table only holds individual stocks today.
    """

    code: str
    name: str
    close: Optional[Decimal] = None
    pct_change: Optional[Decimal] = None


class MarketSummary(BaseModel):
    """Market breadth on the latest trading day.

    ``advance``/``decline``/``flat`` partition the day's stocks by the sign of
    ``pct_change``; ``total_amount`` is the sum of turnover across all stocks.
    """

    trade_date: Optional[date] = None
    advance_count: int = 0
    decline_count: int = 0
    flat_count: int = 0
    total_amount: Optional[Decimal] = None


class HotStock(BaseModel):
    """A single row of the hot-stocks leaderboard (top gainers / most active).

    ``stock_name`` is joined from the latest ``stock_pool`` snapshot because
    ``daily_prices`` carries code+OHLCV only.
    """

    stock_code: str
    stock_name: Optional[str] = None
    close: Optional[Decimal] = None
    pct_change: Optional[Decimal] = None
    amount: Optional[Decimal] = None
