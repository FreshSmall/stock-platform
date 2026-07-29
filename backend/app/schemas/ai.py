"""Pydantic schemas for the AI analysis pipeline (Task D2).

``AnalysisScores`` is the per-dimension breakdown (0-100 each) and
``AnalysisResult`` is the fully-parsed analysis row, used both as the value
yielded by :func:`app.ai.stock_agent.analyze_stream` and as the payload
serialised into the SSE stream and persisted to ``sa_ai_analysis``.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class AnalysisScores(BaseModel):
    """Per-dimension scores in [0, 100]. ``None`` means "not provided"."""

    fundamental: Optional[Decimal] = None
    technical: Optional[Decimal] = None
    capital: Optional[Decimal] = None
    news: Optional[Decimal] = None
    risk: Optional[Decimal] = None


class AnalysisResult(BaseModel):
    """A fully-parsed analysis result for one stock + request."""

    request_id: str
    stock_code: str
    score: Optional[Decimal] = None
    scores: AnalysisScores = AnalysisScores()
    fundamentals: Optional[str] = None
    technicals: Optional[str] = None
    capital: Optional[str] = None
    news: Optional[str] = None
    risk: Optional[str] = None
    created_at: Optional[datetime] = None
