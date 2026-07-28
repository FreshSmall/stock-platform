"""Tests for the AI assistant service (Task E2).

All LLM access is mocked: ``llm_client.get_llm`` is monkeypatched to return a
scripted fake. The DB is the real ``stock_analysis`` DB, so every test cleans
up the sessions / messages it creates to avoid polluting production data.

The fake LLM mimics the LangChain shape the production code relies on:

* ``bind_tools(tools)`` returns ``self`` (tool binding is a no-op for tests).
* ``invoke(messages)`` returns a fake AIMessage exposing ``.content`` and
  ``.tool_calls`` (a list of plain ``{name, args, id}`` dicts, matching the
  real LangChain ``AIMessage.tool_calls`` shape).
* ``stream(messages)`` is omitted by default, exercising the
  ``AIMessage.content`` fallback; one test adds it to cover token streaming.
"""

import pytest

from app.models.ai import SaAiChatMessage, SaAiChatSession
from app.services import assistant_service


class _FakeAIContent:
    """A minimal stand-in for ``langchain_core.messages.AIMessage``."""

    def __init__(self, content: str, tool_calls: list | None = None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeAIWithTools:
    """Fake LLM whose ``invoke`` returns a scripted sequence of AIMessages."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self._idx = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        msg = self._scripted[self._idx]
        self._idx += 1
        return msg


class _StreamingFakeAI(_FakeAIWithTools):
    """Fake LLM that also streams the final answer token-by-token."""

    class _Chunk:
        def __init__(self, content: str):
            self.content = content

    def stream(self, messages):
        # Only the last scripted message is a final answer; stream its content
        # as 3 chunks so the streaming path is genuinely exercised.
        final = self._scripted[-1].content or ""
        mid = len(final) // 2 or 1
        for piece in (final[:mid], final[mid:], ""):
            if piece:
                yield self._Chunk(piece)


def _cleanup(db_session, session_id: str) -> None:
    """Remove a session and its messages so tests don't pollute prod data."""
    db_session.query(SaAiChatMessage).filter_by(session_id=session_id).delete()
    db_session.query(SaAiChatSession).filter_by(session_id=session_id).delete()
    db_session.commit()


# --------------------------------------------------------------------------- #
# Session / message CRUD
# --------------------------------------------------------------------------- #


def test_session_crud(db_session):
    s = assistant_service.create_session(db_session, user_id=1, title="t1")
    try:
        assert s.session_id.startswith("cs-")
        assert s.user_id == 1
        assert s.title == "t1"

        fetched = assistant_service.get_session(db_session, s.session_id)
        assert fetched is not None
        assert fetched.session_id == s.session_id

        listed = assistant_service.list_sessions(db_session, user_id=1)
        assert any(x.session_id == s.session_id for x in listed)
        # list_sessions is scoped to the user — another user sees nothing.
        other = assistant_service.list_sessions(db_session, user_id=999999)
        assert all(x.session_id != s.session_id for x in other)
    finally:
        _cleanup(db_session, s.session_id)


def test_save_and_list_messages(db_session):
    s = assistant_service.create_session(db_session, user_id=1)
    try:
        assistant_service.save_message(db_session, s.session_id, "user", "你好")
        assistant_service.save_message(
            db_session, s.session_id, "assistant", "你好，有什么可以帮你？"
        )
        msgs = assistant_service.list_messages(db_session, s.session_id)
        roles = [m.role for m in msgs]
        assert roles == ["user", "assistant"]
        assert msgs[0].content == "你好"
    finally:
        _cleanup(db_session, s.session_id)


# --------------------------------------------------------------------------- #
# chat_stream — no tools (content fallback)
# --------------------------------------------------------------------------- #


def test_chat_stream_no_tools(db_session, monkeypatch):
    from app.ai import llm_client

    s = assistant_service.create_session(db_session, user_id=1)
    try:
        fake = _FakeAIWithTools(
            [_FakeAIContent(content="贵州茅台是A股知名白酒股。")]
        )
        monkeypatch.setattr(llm_client, "get_llm", lambda: fake)

        events = list(
            assistant_service.chat_stream(db_session, s.session_id, "介绍一下贵州茅台")
        )
        types = [e[0] for e in events]

        assert types[0] == "user_saved"
        assert "chunk" in types
        assert types[-1] == "done"
        assert "贵州茅台" in events[-1][1]

        # The assistant message must be persisted for multi-turn context.
        msgs = assistant_service.list_messages(db_session, s.session_id)
        assert any(m.role == "assistant" and "贵州茅台" in m.content for m in msgs)
    finally:
        _cleanup(db_session, s.session_id)


# --------------------------------------------------------------------------- #
# chat_stream — Function Calling tool-execution loop
# --------------------------------------------------------------------------- #


def test_chat_stream_with_tool_call(db_session, monkeypatch):
    from app.ai import llm_client

    s = assistant_service.create_session(db_session, user_id=1)
    try:
        # First invoke: model wants to call query_stock_info.
        # Second invoke: final text answer synthesising the tool result.
        fake = _FakeAIWithTools(
            [
                _FakeAIContent(
                    content="",
                    tool_calls=[
                        {
                            "name": "query_stock_info",
                            "args": {"code": "600519"},
                            "id": "tc1",
                        }
                    ],
                ),
                _FakeAIContent(content="贵州茅台（600519）PE约18。"),
            ]
        )
        monkeypatch.setattr(llm_client, "get_llm", lambda: fake)

        events = list(
            assistant_service.chat_stream(db_session, s.session_id, "600519的PE多少")
        )
        types = [e[0] for e in events]

        assert "tool_call" in types
        assert "tool_result" in types
        assert types[-1] == "done"

        tool_call_evt = next(e for e in events if e[0] == "tool_call")
        assert tool_call_evt[1] == {"name": "query_stock_info", "args": {"code": "600519"}}

        tool_result_evt = next(e for e in events if e[0] == "tool_result")
        assert tool_result_evt[1]["name"] == "query_stock_info"
        # The real DB-backed tool returns the known-good 600519 stock info.
        assert tool_result_evt[1]["result"].get("found") is True
        assert "茅台" in (tool_result_evt[1]["result"].get("name") or "")

        assert "600519" in events[-1][1]
    finally:
        _cleanup(db_session, s.session_id)


def test_chat_stream_unknown_tool_records_error(db_session, monkeypatch):
    from app.ai import llm_client

    s = assistant_service.create_session(db_session, user_id=1)
    try:
        fake = _FakeAIWithTools(
            [
                _FakeAIContent(
                    content="",
                    tool_calls=[
                        {"name": "no_such_tool", "args": {}, "id": "x"}
                    ],
                ),
                _FakeAIContent(content="抱歉，暂不支持该查询。"),
            ]
        )
        monkeypatch.setattr(llm_client, "get_llm", lambda: fake)

        events = list(
            assistant_service.chat_stream(db_session, s.session_id, "xxx")
        )
        tool_result_evt = next(e for e in events if e[0] == "tool_result")
        assert "error" in tool_result_evt[1]["result"]
        assert events[-1] == ("done", "抱歉，暂不支持该查询。")
    finally:
        _cleanup(db_session, s.session_id)


def test_chat_stream_streaming_final_answer(db_session, monkeypatch):
    """When the LLM exposes ``stream``, the final answer is chunked."""
    from app.ai import llm_client

    s = assistant_service.create_session(db_session, user_id=1)
    try:
        fake = _StreamingFakeAI([_FakeAIContent(content="streamed-answer")])
        monkeypatch.setattr(llm_client, "get_llm", lambda: fake)

        events = list(
            assistant_service.chat_stream(db_session, s.session_id, "hi")
        )
        chunks = [e[1] for e in events if e[0] == "chunk"]
        # At least 2 chunks emitted, concatenating to the full answer.
        assert len(chunks) >= 2
        assert "".join(chunks) == "streamed-answer"
        assert events[-1] == ("done", "streamed-answer")
    finally:
        _cleanup(db_session, s.session_id)


def test_chat_stream_llm_error_surfaces_error_event(db_session, monkeypatch):
    from app.ai import llm_client

    s = assistant_service.create_session(db_session, user_id=1)
    try:

        class _Boom:
            def bind_tools(self, tools):
                return self

            def invoke(self, messages):
                raise RuntimeError("LLM down")

        monkeypatch.setattr(llm_client, "get_llm", lambda: _Boom())

        events = list(
            assistant_service.chat_stream(db_session, s.session_id, "hi")
        )
        types = [e[0] for e in events]
        assert "error" in types
        assert types[-1] == "done"
        assert events[-1][1] == ""  # graceful empty done
    finally:
        _cleanup(db_session, s.session_id)


def test_chat_stream_history_feeds_back(db_session, monkeypatch):
    """A prior assistant turn is part of the message list sent to the LLM."""
    from app.ai import llm_client

    s = assistant_service.create_session(db_session, user_id=1)
    try:
        assistant_service.save_message(db_session, s.session_id, "user", "上次聊到茅台")
        assistant_service.save_message(
            db_session, s.session_id, "assistant", "好的，茅台是白酒龙头"
        )

        captured: list = []

        class _Capture:
            def bind_tools(self, tools):
                return self

            def invoke(self, messages):
                captured.append(list(messages))
                return _FakeAIContent(content="收到")

        monkeypatch.setattr(llm_client, "get_llm", lambda: _Capture())

        list(assistant_service.chat_stream(db_session, s.session_id, "继续"))
        # System + 2 history + new user message.
        last_msgs = captured[-1]
        assert last_msgs[0].content == assistant_service.ASSISTANT_SYSTEM_PROMPT
        assert any(getattr(m, "content", "") == "好的，茅台是白酒龙头" for m in last_msgs)
        assert any(getattr(m, "content", "") == "继续" for m in last_msgs)
    finally:
        _cleanup(db_session, s.session_id)
