"""Analysis service: request lifecycle, rate limiting, persistence.

This layer sits between the FastAPI router and the agent:

* Hands out correlation ids (``create_request_id``).
* Enforces a per-stock cooldown (``_rate_limit``) via a process-local
  :class:`TTLCache`, returning HTTP 429 if the same code was analysed inside
  the cooldown window.
* Persists a parsed :class:`AnalysisResult` to ``sa_ai_analysis``.
* Reads back the latest analysis for a code (``get_latest``).
"""

from __future__ import annotations

import logging
import uuid

from cachetools import TTLCache
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai import SaAiAnalysis
from app.schemas.ai import AnalysisResult

logger = logging.getLogger(__name__)

# Per-stock cooldown window. Residing in a process-local TTLCache means the
# limit is per-process (fine for the MVP single-worker deployment); a future
# multi-worker setup should move this to Redis.
COOLDOWN_SECONDS = 600
_cooldown: TTLCache = TTLCache(maxsize=4096, ttl=COOLDOWN_SECONDS)


def _rate_limit(code: str) -> None:
    """Raise HTTP 429 if ``code`` was analysed within the cooldown window."""
    if code in _cooldown:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="该股票分析冷却中，请稍后再试",
        )
    _cooldown[code] = True


def create_request_id() -> str:
    """Return a new analysis request correlation id (``an-<12 hex chars>``)."""
    return f"an-{uuid.uuid4().hex[:12]}"


def persist_result(
    db: Session, request_id: str, user_id: int | None, result: AnalysisResult
) -> SaAiAnalysis:
    """Insert a single :class:`AnalysisResult` row and return the refreshed ORM object."""
    row = SaAiAnalysis(
        request_id=request_id,
        stock_code=result.stock_code,
        user_id=user_id,
        score=result.score,
        score_fundamental=result.scores.fundamental,
        score_technical=result.scores.technical,
        score_capital=result.scores.capital,
        score_news=result.scores.news,
        score_risk=result.scores.risk,
        fundamentals=result.fundamentals,
        technicals=result.technicals,
        capital=result.capital,
        news=result.news,
        risk=result.risk,
        full_text=None,  # could store the raw stream if/when needed.
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_latest(db: Session, code: str) -> SaAiAnalysis | None:
    """Return the most-recent analysis row for ``code`` (by ``created_at``)."""
    return db.execute(
        select(SaAiAnalysis)
        .where(SaAiAnalysis.stock_code == code)
        .order_by(SaAiAnalysis.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
