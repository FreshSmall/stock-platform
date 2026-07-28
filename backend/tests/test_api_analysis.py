"""API tests for the analysis router (Task D2).

The LLM is mocked via monkeypatch so the SSE stream runs without any network.
The DB is the real ``stock_analysis`` DB; any row persisted during a stream
test is deleted in the same test.
"""

from fastapi.testclient import TestClient

from app.ai import llm_client
from app.core.database import SessionLocal
from app.main import app
from app.models.ai import SaAiAnalysis
from app.services import analysis_service

client = TestClient(app)

FAKE_JSON = (
    '{"score": 75, "scores": {"fundamental":80,"technical":70,"capital":65,'
    '"news":72,"risk":60}, "fundamentals":"ok", "technicals":"ok", '
    '"capital":"ok", "news":"ok", "risk":"ok"}'
)


def test_api_trigger_returns_request_id():
    """POST /{code} returns 200 + a request_id starting with ``an-``."""
    analysis_service._cooldown.pop("600519", None)
    resp = client.post(
        "/api/v1/analysis/600519", headers={"Authorization": "Bearer test"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["request_id"].startswith("an-")
    # cleanup cooldown state so other tests aren't affected.
    analysis_service._cooldown.pop("600519", None)


def test_api_latest_returns_null_when_absent():
    """GET /{code}/latest on a code with no history returns code=0 + null data."""
    resp = client.get("/api/v1/analysis/__NOPE__/latest")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"] is None


def test_api_stream_yields_sse_events(monkeypatch):
    """GET /{code}/stream emits SSE chunks + a done + a disclaimer event."""
    monkeypatch.setattr(
        llm_client, "stream_chat", lambda messages: iter([FAKE_JSON])
    )
    analysis_service._cooldown.pop("600519", None)

    with client.stream(
        "GET",
        "/api/v1/analysis/600519/stream?request_id=an-test-stream",
        headers={"Authorization": "Bearer test"},
    ) as resp:
        assert resp.status_code == 200
        text = ""
        for line in resp.iter_lines():
            text += line + "\n"

    assert "data:" in text
    assert "chunk" in text
    assert "done" in text
    assert "disclaimer" in text

    # cleanup any persisted row.
    db = SessionLocal()
    try:
        db.query(SaAiAnalysis).filter_by(request_id="an-test-stream").delete()
        db.commit()
    finally:
        db.close()
    analysis_service._cooldown.pop("600519", None)


def test_api_trigger_429_within_cooldown():
    """A second POST for the same code within the cooldown returns 429."""
    code = "600519_APIRL"
    analysis_service._cooldown.pop(code, None)

    r1 = client.post(
        f"/api/v1/analysis/{code}", headers={"Authorization": "Bearer test"}
    )
    assert r1.status_code == 200

    r2 = client.post(
        f"/api/v1/analysis/{code}", headers={"Authorization": "Bearer test"}
    )
    assert r2.status_code == 429

    analysis_service._cooldown.pop(code, None)
