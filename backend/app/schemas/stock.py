"""Pydantic v2 response schemas for the market/stock API (Task B1).

These models shape the ``data`` field of the unified response envelope (see
:mod:`app.schemas.common`). They are intentionally permissive (most fields are
``Optional``) because the underlying read-only ``stock_pool`` / ``daily_prices``
tables legitimately contain NULLs for some stocks.
"""

from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class StockBrief(BaseModel):
    """Compact stock summary used in search results."""

    stock_code: str
    stock_name: Optional[str] = None
    exchange: Optional[str] = None
    industry: Optional[str] = None


class StockInfo(StockBrief):
    """Detailed stock snapshot sourced from the latest ``stock_pool`` row."""

    total_mv: Optional[Decimal] = None
    circ_mv: Optional[Decimal] = None
    pe: Optional[Decimal] = None
    pb: Optional[Decimal] = None
    list_date: Optional[date] = None
    close: Optional[Decimal] = None
    pct_change: Optional[Decimal] = None


class KLineItem(BaseModel):
    """A single daily OHLCV bar from ``daily_prices``."""

    trade_date: date
    open: Optional[Decimal] = None
    close: Optional[Decimal] = None
    high: Optional[Decimal] = None
    low: Optional[Decimal] = None
    volume: Optional[int] = None
    amount: Optional[Decimal] = None
    pct_change: Optional[Decimal] = None
    turnover: Optional[Decimal] = None
