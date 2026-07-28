"""AI assistant router (Task E2).

Mounted under ``/api/v1/assistant``. Endpoints:

* ``POST /sessions``                       - create a new chat session.
* ``GET  /sessions``                       - list the caller's sessions.
* ``GET  /sessions/{session_id}/messages`` - full message history.
* ``POST /sessions/{session_id}/messages`` - send a message and stream the
  assistant reply back as Server-Sent Events (one ``data: <json>`` block per
  service event plus a trailing ``disclaimer`` event).
"""

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.prompts import RISK_DISCLAIMER
from app.core.deps import get_current_user_id, get_db
from app.core.ratelimit import check_assistant_quota
from app.services import assistant_service

router = APIRouter(prefix="/assistant", tags=["assistant"])


def _ok(data=None, msg: str = "ok") -> dict:
    """Build the unified success envelope (lazy import to avoid a cycle)."""
    from app.main import api_ok

    return api_ok(data, msg)


def _sse(obj: dict) -> str:
    """Render one SSE message as ``data: <json>\\n\\n`` (utf-8 safe)."""
    return f"data: {json.dumps(obj, ensure_ascii=False, default=str)}\n\n"


class CreateSessionIn(BaseModel):
    """Body for ``POST /sessions``."""

    title: str | None = None


class SendMessageIn(BaseModel):
    """Body for ``POST /sessions/{id}/messages``."""

    content: str


@router.post("/sessions")
def create_session(
    payload: CreateSessionIn,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> dict:
    """Create a new chat session owned by the current user."""
    s = assistant_service.create_session(db, user_id, payload.title)
    return _ok({"session_id": s.session_id, "title": s.title})


@router.get("/sessions")
def list_sessions(
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> dict:
    """List all sessions belonging to the current user, newest first."""
    sessions = assistant_service.list_sessions(db, user_id)
    return _ok(
        [
            {
                "session_id": s.session_id,
                "title": s.title,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in sessions
        ]
    )


@router.get("/sessions/{session_id}/messages")
def get_messages(
    session_id: str, db: Session = Depends(get_db)
) -> dict:
    """Return the full message history of one session."""
    msgs = assistant_service.list_messages(db, session_id)
    return _ok(
        [
            {
                "role": m.role,
                "content": m.content,
                "tool_calls": m.tool_calls,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in msgs
        ]
    )


@router.post("/sessions/{session_id}/messages")
def send_message(
    session_id: str,
    payload: SendMessageIn,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> StreamingResponse:
    """Send a message and stream the assistant reply via SSE.

    Each ``(type, data)`` event from
    :func:`assistant_service.chat_stream` becomes one ``data: <json>`` block;
    a ``disclaimer`` event always closes the stream.
    """
    session = assistant_service.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="forbidden")

    # Per-USER token bucket (F2); checked after ownership so a 404/403 does not
    # burn the caller's assistant quota.
    check_assistant_quota(user_id)

    def event_gen():
        for evt_type, data in assistant_service.chat_stream(
            db, session_id, payload.content
        ):
            yield _sse({"type": evt_type, "data": data})
        yield _sse({"type": "disclaimer", "data": RISK_DISCLAIMER})

    return StreamingResponse(event_gen(), media_type="text/event-stream")
