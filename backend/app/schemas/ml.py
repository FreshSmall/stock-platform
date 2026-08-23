"""Request schema for the meta-labeling API (BP-ML-001)."""

from datetime import date

from pydantic import BaseModel, Field


class MetaLabelRequest(BaseModel):
    """Run trendline-breakout + random-forest meta-labeling on given stocks.

    ``pt`` / ``sl`` are the upper/lower barrier widths; ``prob_th`` is the
    minimum predicted win probability to take a trade.
    """

    stock_codes: list[str] = Field(..., min_length=1, max_length=50)
    start: date | None = None
    end: date | None = None
    pt: float = Field(0.04, gt=0, lt=0.5)
    sl: float = Field(0.02, gt=0, lt=0.5)
    horizon: int = Field(10, ge=2, le=60)
    prob_th: float = Field(0.5, gt=0.3, lt=1.0)
    order: int = Field(3, ge=2, le=10)
    init_train: int = Field(200, ge=30, le=2000)
    step: int = Field(50, ge=5, le=500)
    embargo_days: int = Field(14, ge=0, le=60)
    # volatility-scaled barriers (2×ATR up / 1×ATR down) instead of fixed pt/sl
    atr_barriers: bool = False
