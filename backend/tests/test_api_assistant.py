"""API tests for the /assistant endpoints (Task E2).

The DB is the real ``stock_analysis`` DB; every test cleans up the session /
messages it creates. The LLM is mocked via ``monkeypatch`` on
``llm_client.get_llm`` — no real LLM key is required.
"""

import json

from fastapi.testclient import TestClient

from app.ai import llm_client
from app.core.database import SessionLocal
from app.main import app
from app.models.ai import SaAiChatMessage, SaAiChatSession

client = TestClient(app)


class _FakeAIMsg:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeLLM:
    """Fake LLM: returns one scripted AIMessage per ``invoke`` call."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self._i = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        msg = self._scripted[self._i]
        self._i += 1
        return msg


def _cleanup(session_id: str) -> None:
    db = SessionLocal()
    try:
        db.query(SaAiChatMessage).filter_by(session_id=session_id).delete()
        db.query(SaAiChatSession).filter_by(session_id=session_id).delete()
        db.commit()
    finally:
        db.close()


def _read_stream(response) -> list[dict]:
    """Parse an SSE response body into a list of event dicts."""
    events = []
    for line in response.iter_lines():
        if not line or not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        events.append(json.loads(payload))
    return events


def test_api_create_and_list_session(auth_headers):
    r = client.post("/api/v1/assistant/sessions", json={"title": "t"}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    sid = body["data"]["session_id"]
    assert sid.startswith("cs-")
    assert body["data"]["title"] == "t"
    try:
        r2 = client.get("/api/v1/assistant/sessions", headers=auth_headers)
        assert r2.status_code == 200
        assert any(s["session_id"] == sid for s in r2.json()["data"])
    finally:
        _cleanup(sid)


def test_api_get_messages_history(auth_headers):
    sid = client.post(
        "/api/v1/assistant/sessions", json={"title": "h"}, headers=auth_headers
    ).json()["data"]["session_id"]
    try:
        r = client.get(f"/api/v1/assistant/sessions/{sid}/messages", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["data"] == []  # nothing yet
    finally:
        _cleanup(sid)


def test_api_get_messages_requires_auth():
    # No token → 401
    r = client.get("/api/v1/assistant/sessions/cs-doesnotexist/messages")
    assert r.status_code == 401


def test_api_get_messages_forbidden_for_other_owner(auth_headers):
    # Create a session as user A, then try to read it as user B.
    sid = client.post(
        "/api/v1/assistant/sessions", json={"title": "h"}, headers=auth_headers
    ).json()["data"]["session_id"]
    try:
        # Register a second user and get a token.
        import uuid as _uuid
        other_user = f"other_{_uuid.uuid4().hex[:8]}"
        client.post("/api/v1/auth/register", json={"username": other_user, "password": "Other123!"})
        other_token = client.post(
            "/api/v1/auth/login", json={"username": other_user, "password": "Other123!"}
        ).json()["data"]["token"]
        r = client.get(
            f"/api/v1/assistant/sessions/{sid}/messages",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert r.status_code == 403
        # cleanup other user
        from app.core.database import SessionLocal
        from app.models.user import SaUser
        db = SessionLocal()
        db.query(SaUser).filter_by(username=other_user).delete()
        db.commit()
        db.close()
    finally:
        _cleanup(sid)


def test_api_send_message_streams(monkeypatch, auth_headers):
    sid = client.post(
        "/api/v1/assistant/sessions", json={"title": "t"}, headers=auth_headers
    ).json()["data"]["session_id"]
    try:
        # No-tools LLM -> exercises the content fallback chunk path.
        fake = _FakeLLM([_FakeAIMsg(content="测试回复")])
        monkeypatch.setattr(llm_client, "get_llm", lambda: fake)

        with client.stream(
            "POST",
            f"/api/v1/assistant/sessions/{sid}/messages",
            json={"content": "你好"},
            headers=auth_headers,
        ) as r:
            assert r.status_code == 200
            events = _read_stream(r)

        types = [e["type"] for e in events]
        assert "user_saved" in types
        assert "chunk" in types
        assert types[-1] == "disclaimer"
        done = next(e for e in events if e["type"] == "done")
        assert done["data"] == "测试回复"
    finally:
        _cleanup(sid)


def test_api_send_message_tool_flow_visible(monkeypatch, auth_headers):
    sid = client.post(
        "/api/v1/assistant/sessions", json={"title": "tc"}, headers=auth_headers
    ).json()["data"]["session_id"]
    try:
        fake = _FakeLLM(
            [
                _FakeAIMsg(
                    content="",
                    tool_calls=[
                        {
                            "name": "query_stock_info",
                            "args": {"code": "600519"},
                            "id": "tc1",
                        }
                    ],
                ),
                _FakeAIMsg(content="贵州茅台PE约18。"),
            ]
        )
        monkeypatch.setattr(llm_client, "get_llm", lambda: fake)

        with client.stream(
            "POST",
            f"/api/v1/assistant/sessions/{sid}/messages",
            json={"content": "查 600519"},
            headers=auth_headers,
        ) as r:
            assert r.status_code == 200
            events = _read_stream(r)

        types = [e["type"] for e in events]
        # The Function Calling round-trip must be visible to the client.
        assert "tool_call" in types
        assert "tool_result" in types
        assert "done" in types
        assert types[-1] == "disclaimer"
    finally:
        _cleanup(sid)


def test_api_send_message_404_unknown_session(auth_headers):
    r = client.post(
        "/api/v1/assistant/sessions/cs-does-not-exist/messages",
        json={"content": "hi"},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_api_missing_token_unauthorized():
    r = client.post("/api/v1/assistant/sessions", json={"title": "t"})
    assert r.status_code == 401
