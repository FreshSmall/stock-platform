"""AI assistant router (Task E2 + V2 阶段 L 知识库).

Mounted under ``/api/v1/assistant``. Endpoints:

* ``POST /sessions``                       - create a new chat session.
* ``GET  /sessions``                       - list the caller's sessions.
* ``GET  /sessions/{session_id}/messages`` - full message history.
* ``POST /sessions/{session_id}/messages`` - send a message and stream the
  assistant reply back as Server-Sent Events (one ``data: <json>`` block per
  service event plus a trailing ``disclaimer`` event). 若命中知识库则自动走
  RAG 回答。
* ``POST /assistant/knowledge``            - 上传知识库文档并触发入库向量化。
* ``GET  /assistant/knowledge``            - 知识库文档列表。
* ``DELETE /assistant/knowledge/{doc_id}`` - 删除文档及其分块。
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai import rag
from app.ai.prompts import RISK_DISCLAIMER
from app.core.deps import get_current_user_id, get_db
from app.core.ratelimit import check_assistant_quota
from app.services import assistant_service, knowledge_service

logger = logging.getLogger(__name__)

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


class UploadDocIn(BaseModel):
    """Body for ``POST /assistant/knowledge``：上传一篇知识库文档。"""

    title: str
    content: str
    source: str | None = None
    stock_code: str | None = None
    doc_date: str | None = None  # ISO 日期字符串，可选


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
    session_id: str,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> dict:
    """Return the full message history of one session (owner only)."""
    session = assistant_service.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="forbidden")
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

    # 先尝试命中知识库（RAG）：若检索到相关资料，直接用 RAG 回答并标注来源；
    # 否则回退到带工具的常规对话。RAG 内部对 LLM/检索异常做了容错，失败时
    # 同样回退到常规对话，保证不阻断主链路。
    try:
        answer, sources = rag.answer_with_rag(db, payload.content)
    except Exception:  # noqa: BLE001 - RAG 失败不能阻断主对话。
        logger.exception("rag failed, falling back to tool chat")
        answer, sources = "", []

    def event_gen():
        if answer:
            # RAG 命中：先持久化用户消息与助手回答，再一次性吐出 chunk + 来源。
            assistant_service.save_message(db, session_id, "user", payload.content)
            yield _sse({"type": "user_saved", "data": None})
            yield _sse({"type": "chunk", "data": answer})
            yield _sse({"type": "sources", "data": sources})
            assistant_service.save_message(db, session_id, "assistant", answer)
            yield _sse({"type": "done", "data": answer})
        else:
            for evt_type, data in assistant_service.chat_stream(
                db, session_id, payload.content
            ):
                yield _sse({"type": evt_type, "data": data})
        yield _sse({"type": "disclaimer", "data": RISK_DISCLAIMER})

    return StreamingResponse(event_gen(), media_type="text/event-stream")


# --------------------------------------------------------------------------- #
# 知识库管理 (V2 阶段 L)
# --------------------------------------------------------------------------- #


@router.post("/knowledge")
def upload_knowledge_doc(
    payload: UploadDocIn,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> dict:
    """上传一篇知识库文档，触发分块 + 向量化入库。

    成功返回文档 id / status；若 embedding 调用失败，文档会被标记为
    ``failed`` 并抛出 422（仍保留文档记录，便于重试）。
    """
    from datetime import date as _date

    doc_date = None
    if payload.doc_date:
        try:
            doc_date = _date.fromisoformat(payload.doc_date)
        except ValueError:
            raise HTTPException(
                status_code=422, detail="doc_date 必须是 ISO 日期 (YYYY-MM-DD)"
            )

    try:
        doc = knowledge_service.ingest_doc(
            db,
            title=payload.title,
            content=payload.content,
            source=payload.source,
            stock_code=payload.stock_code,
            doc_date=doc_date,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return _ok(
        {"id": doc.id, "title": doc.title, "status": doc.status},
        msg="ingested",
    )


@router.get("/knowledge")
def list_knowledge_docs(
    stock_code: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> dict:
    """列出知识库文档，可按 ``stock_code`` / ``status`` 过滤。"""
    docs = knowledge_service.list_docs(db, stock_code=stock_code, status=status, limit=limit)
    return _ok(
        [
            {
                "id": d.id,
                "title": d.title,
                "source": d.source,
                "stock_code": d.stock_code,
                "doc_date": d.doc_date.isoformat() if d.doc_date else None,
                "status": d.status,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in docs
        ]
    )


@router.delete("/knowledge/{doc_id}")
def delete_knowledge_doc(
    doc_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> dict:
    """删除一篇知识库文档及其所有分块。"""
    deleted = knowledge_service.delete_doc(db, doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="doc not found")
    return _ok(None, msg="deleted")
