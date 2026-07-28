"""AI assistant service: session/message CRUD + tool-calling chat loop.

Pipeline of :func:`chat_stream` (one assistant turn):

1. Persist the incoming user message and reload the recent multi-turn history.
2. Bind the :data:`app.ai.tools.ALL_TOOLS` to the LLM.
3. Loop (capped at :data:`MAX_TOOL_ITERS`):

   * ``invoke`` the model with the current message list.
   * If the AIMessage carries ``tool_calls``, execute each tool, emit a
     ``tool_call`` / ``tool_result`` event pair, append a :class:`ToolMessage`
     and re-invoke (Function Calling tool-execution loop).
   * Otherwise stream the final text answer back to the caller as ``chunk``
     events, persist it as the assistant message and finish with ``done``.

Design note on streaming: per the V1 spec we use ``invoke`` (non-streaming)
per turn so that ``tool_calls`` are captured cleanly, then stream the final
answer token-by-token via ``llm.stream``. This keeps the Function Calling
flow visible (``tool_call`` + ``tool_result`` events) while still giving the
client incremental text for the final reply.

All LLM access is funnelled through :func:`app.ai.llm_client.get_llm`, so
tests monkeypatch it with a fake; no real LLM key is required.
"""

import json
import logging
import uuid
from typing import Any, Iterator

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import llm_client
from app.ai.tools import ALL_TOOLS
from app.models.ai import SaAiChatMessage, SaAiChatSession

logger = logging.getLogger(__name__)

ASSISTANT_SYSTEM_PROMPT = """你是 AI 股票分析助手，可以帮助用户查询 A 股行情、技术指标、回测策略。
规则：
1. 需要具体数据时，调用提供的工具（query_kline / query_stock_info / query_macd / search_stocks_by_keyword / run_backtest_light）。
2. 回答要简洁、专业，使用 markdown。
3. 涉及具体买卖建议时，必须附上风险提示。
4. 不要编造数据，工具未返回的信息要明确说明。"""

MAX_TOOL_ITERS = 5  # guard against infinite tool loops

# Tool-call results can be non-JSON (e.g. a Tool that returns a plain str);
# json.dumps with default=str keeps the loop robust against that.
_TOOL_BY_NAME = {t.name: t for t in ALL_TOOLS}

# Only the most recent turns are fed back to the LLM, both to bound token cost
# and because older tool exchanges carry stale args/results.
_HISTORY_WINDOW = 10


# --------------------------------------------------------------------------- #
# Session / message CRUD
# --------------------------------------------------------------------------- #


def create_session(
    db: Session, user_id: int, title: str | None = None
) -> SaAiChatSession:
    """Create and persist a new chat session owned by ``user_id``."""
    session = SaAiChatSession(
        session_id=f"cs-{uuid.uuid4().hex[:12]}",
        user_id=user_id,
        title=title,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def list_sessions(db: Session, user_id: int) -> list[SaAiChatSession]:
    """Return all sessions for ``user_id``, newest first."""
    return list(
        db.execute(
            select(SaAiChatSession)
            .where(SaAiChatSession.user_id == user_id)
            .order_by(SaAiChatSession.created_at.desc())
        )
        .scalars()
        .all()
    )


def get_session(db: Session, session_id: str) -> SaAiChatSession | None:
    """Look up a single session by its public ``session_id``."""
    return db.execute(
        select(SaAiChatSession).where(SaAiChatSession.session_id == session_id)
    ).scalar_one_or_none()


def list_messages(db: Session, session_id: str) -> list[SaAiChatMessage]:
    """Return all messages of a session in chronological order."""
    return list(
        db.execute(
            select(SaAiChatMessage)
            .where(SaAiChatMessage.session_id == session_id)
            .order_by(SaAiChatMessage.created_at.asc())
        )
        .scalars()
        .all()
    )


def save_message(
    db: Session,
    session_id: str,
    role: str,
    content: str,
    tool_calls: dict | list | None = None,
) -> SaAiChatMessage:
    """Persist one message (``role`` in {user, assistant, tool})."""
    msg = SaAiChatMessage(
        session_id=session_id,
        role=role,
        content=content,
        tool_calls=(
            json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None
        ),
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def _load_history(db: Session, session_id: str) -> list:
    """Build the LangChain message list from persisted history.

    Always starts with the system prompt, then the last
    :data:`_HISTORY_WINDOW` messages. Tool messages have no stored
    ``tool_call_id`` in the current schema, so a synthetic id is used — this is
    fine because the historical ToolMessages are never re-executed, only read
    as context by the model.
    """
    rows = list_messages(db, session_id)[-_HISTORY_WINDOW:]
    msgs: list = [SystemMessage(content=ASSISTANT_SYSTEM_PROMPT)]
    for r in rows:
        if r.role == "user":
            msgs.append(HumanMessage(content=r.content))
        elif r.role == "assistant":
            msgs.append(AIMessage(content=r.content))
        elif r.role == "tool":
            msgs.append(ToolMessage(content=r.content, tool_call_id="hist"))
    return msgs


# --------------------------------------------------------------------------- #
# Chat loop (streaming + tools)
# --------------------------------------------------------------------------- #


def chat_stream(
    db: Session, session_id: str, user_text: str
) -> Iterator[tuple[str, Any]]:
    """Generator yielding SSE events for one assistant turn.

    Events (``(type, data)`` tuples):

    * ``("user_saved", None)``                - the user message is persisted.
    * ``("tool_call", {name, args})``         - the model decided to call a tool.
    * ``("tool_result", {name, result})``     - that tool has executed.
    * ``("chunk", str)``                      - a text fragment of the final answer.
    * ``("done", str)``                       - the full final answer text.
    * ``("error", str)``                      - a recoverable error message.

    The stream always terminates with a ``done`` event (possibly with an empty
    string on failure) so the caller can close the SSE response cleanly.
    """
    # Persist the user's turn first so multi-turn history is consistent even
    # if the LLM call below raises.
    save_message(db, session_id, "user", user_text)
    yield ("user_saved", None)

    messages = _load_history(db, session_id)

    try:
        llm = llm_client.get_llm()
        llm_with_tools = (
            llm.bind_tools(ALL_TOOLS) if hasattr(llm, "bind_tools") else llm
        )

        for _ in range(MAX_TOOL_ITERS):
            # Non-streaming invoke per turn so tool_calls are captured cleanly.
            ai_msg = llm_with_tools.invoke(messages)

            tool_calls = getattr(ai_msg, "tool_calls", None) or []
            if not tool_calls:
                # Final text answer. Stream it token-by-token so the client
                # gets incremental output rather than one big blob. Fall back to
                # the already-complete content if the model doesn't support
                # streaming (e.g. a test fake).
                messages.append(ai_msg)
                full = yield from _stream_chunks(llm, messages)
                if not full:
                    content = getattr(ai_msg, "content", None)
                    full = content or ""
                    if full:
                        yield ("chunk", full)
                save_message(db, session_id, "assistant", full)
                yield ("done", full)
                return

            # There are tool calls — execute each, surface the round-trip to
            # the client and feed the results back into the conversation.
            messages.append(ai_msg)
            for tc in tool_calls:
                name, args, tc_id = _unpack_tool_call(tc)
                yield ("tool_call", {"name": name, "args": args})

                tool = _TOOL_BY_NAME.get(name)
                if tool is None:
                    result: Any = {"error": f"unknown tool {name}"}
                else:
                    try:
                        result = tool.invoke(args)
                    except Exception as e:  # noqa: BLE001 - surface to model
                        logger.exception("tool %s failed", name)
                        result = {"error": str(e)}

                yield ("tool_result", {"name": name, "result": result})
                messages.append(
                    ToolMessage(
                        content=json.dumps(
                            result, ensure_ascii=False, default=str
                        ),
                        tool_call_id=tc_id,
                    )
                )

        # Exceeded the iteration cap without a final answer.
        yield ("error", "超过最大工具调用次数")
        yield ("done", "")
    except Exception as e:  # noqa: BLE001 - never let the stream die silently.
        logger.exception("assistant chat failed")
        yield ("error", str(e))
        yield ("done", "")


def _stream_chunks(llm: Any, messages: list) -> Iterator[tuple[str, str]]:
    """Yield ``("chunk", str)`` events for the final (tool-free) reply.

    Streams the model token-by-token. The concatenated full text is returned
    via ``return`` so the caller (using ``yield from``) recovers it. If the
    LLM doesn't expose ``stream`` (e.g. a test fake), nothing is yielded and
    the empty return lets the caller fall back to ``AIMessage.content``.
    """
    if not hasattr(llm, "stream"):
        return ""
    parts: list[str] = []
    try:
        for chunk in llm.stream(messages):
            content = getattr(chunk, "content", None)
            if not content:
                continue
            parts.append(content)
            yield ("chunk", content)
    except Exception:  # noqa: BLE001 - streaming is best-effort; fall back.
        return ""
    return "".join(parts)


def _unpack_tool_call(tc: Any) -> tuple[str, dict, str]:
    """Normalise a LangChain tool_call (dict or object) into ``(name, args, id)``.

    LangChain's :attr:`AIMessage.tool_calls` are plain dicts today
    (``{name, args, id, type}``), but accepting both shapes guards against a
    future switch to a namedtuple-style object.
    """
    if isinstance(tc, dict):
        name = tc.get("name")
        args = tc.get("args") or {}
        tc_id = tc.get("id") or "tc"
    else:
        name = getattr(tc, "name", None)
        args = getattr(tc, "args", None) or {}
        tc_id = getattr(tc, "id", None) or "tc"
    return name, args, tc_id
