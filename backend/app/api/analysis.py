"""AI analysis router (Task D2).

Mounted under ``/api/v1/analysis``. Three endpoints:

* ``POST /{code}``        - rate-limit + hand back a request id.
* ``GET  /{code}/latest`` - the most recent persisted analysis for the code.
* ``GET  /{code}/stream?request_id=...`` - Server-Sent Events stream of a
  fresh analysis run, with one ``data: <json>`` event per agent yield and a
  final ``disclaimer`` event.
"""

import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.ai import stock_agent
from app.core.deps import get_current_user_id, get_db
from app.schemas.ai import AnalysisResult
from app.services import analysis_service

router = APIRouter(prefix="/analysis", tags=["analysis"])


def _ok(data=None, msg: str = "ok") -> dict:
    """Build the unified success envelope (lazy import to avoid a cycle)."""
    from app.main import api_ok

    return api_ok(data, msg)


def _sse(obj: dict) -> str:
    """Render one SSE message as ``data: <json>\\n\\n`` (utf-8 safe)."""
    return f"data: {json.dumps(obj, ensure_ascii=False, default=str)}\n\n"


def _result_to_dict(r: AnalysisResult) -> dict:
    """Flatten an :class:`AnalysisResult` for the SSE ``done`` payload."""
    return {
        "request_id": r.request_id,
        "stock_code": r.stock_code,
        "score": float(r.score) if r.score is not None else None,
        "scores": {
            k: (
                float(getattr(r.scores, k))
                if getattr(r.scores, k) is not None
                else None
            )
            for k in ("fundamental", "technical", "capital", "news", "risk")
        },
    }


@router.post("/{code}")
def trigger(
    code: str,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> dict:
    """Enforce the per-stock cooldown and return a fresh ``request_id``."""
    analysis_service._rate_limit(code)
    request_id = analysis_service.create_request_id()
    return _ok({"request_id": request_id, "stock_code": code})


@router.get("/{code}/latest")
def latest(code: str, db: Session = Depends(get_db)) -> dict:
    """Return the most recent persisted analysis for ``code`` (or null)."""
    row = analysis_service.get_latest(db, code)
    if row is None:
        return _ok(None, msg="no analysis yet")
    return _ok(
        {
            "request_id": row.request_id,
            "stock_code": row.stock_code,
            "score": float(row.score) if row.score is not None else None,
            "scores": {
                "fundamental": float(row.score_fundamental)
                if row.score_fundamental is not None
                else None,
                "technical": float(row.score_technical)
                if row.score_technical is not None
                else None,
                "capital": float(row.score_capital)
                if row.score_capital is not None
                else None,
                "news": float(row.score_news) if row.score_news is not None else None,
                "risk": float(row.score_risk) if row.score_risk is not None else None,
            },
            "fundamentals": row.fundamentals,
            "technicals": row.technicals,
            "capital": row.capital,
            "news": row.news,
            "risk": row.risk,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
    )


@router.get("/{code}/stream")
def stream(
    code: str,
    request_id: str,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> StreamingResponse:
    """Server-Sent Events stream of one analysis run.

    Each agent event (``context`` / ``chunk`` / ``error`` / ``done``) is
    serialised as one ``data: <json>\\n\\n`` block. On a successful parse the
    ``done`` payload carries the structured result and the row is persisted;
    a ``disclaimer`` event always closes the stream.
    """
    from app.ai.prompts import RISK_DISCLAIMER

    def event_gen():
        try:
            for evt_type, payload in stock_agent.analyze_stream(db, code):
                if evt_type == "done" and payload is not None:
                    payload.request_id = request_id
                    analysis_service.persist_result(db, request_id, user_id, payload)
                    yield _sse({"type": "done", "data": _result_to_dict(payload)})
                else:
                    yield _sse({"type": evt_type, "data": payload})
            yield _sse({"type": "disclaimer", "data": RISK_DISCLAIMER})
        except Exception as e:  # never let the SSE stream die mid-flight silently.
            yield _sse({"type": "error", "data": str(e)})

    return StreamingResponse(event_gen(), media_type="text/event-stream")
