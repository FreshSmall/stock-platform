"""Tests for the stock analysis agent + analysis service (Task D2).

ALL LLM access is mocked: ``llm_client.stream_chat`` is patched to yield
predetermined chunks, so the agent runs end-to-end with no network. The DB
fixture hits the real ``stock_analysis`` DB; persisted rows are cleaned up
in the same test that writes them.
"""

from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.ai import llm_client, stock_agent
from app.models.ai import SaAiAnalysis
from app.services import analysis_service

# A complete, valid analysis JSON object the fake LLM will "return".
_FAKE_JSON = (
    '{"score": 75, "scores": {"fundamental":80,"technical":70,"capital":65,'
    '"news":72,"risk":60}, "fundamentals":"基本面尚可", "technicals":"MACD走平", '
    '"capital":"主力净流出", "news":"无重大消息", "risk":"估值偏高"}'
)


def _patch_llm(monkeypatch, chunks):
    """Patch ``llm_client.stream_chat`` to yield ``chunks`` (no network)."""
    monkeypatch.setattr(
        llm_client, "stream_chat", lambda messages: iter(chunks)
    )


def test_analyze_stream_parses_result(db_session, monkeypatch):
    """The agent yields context + chunks + a final parsed result."""
    _patch_llm(monkeypatch, [_FAKE_JSON])
    events = list(stock_agent.analyze_stream(db_session, "600519"))

    types = [e[0] for e in events]
    assert "context" in types
    assert "chunk" in types
    assert types[-1] == "done"

    result = events[-1][1]
    assert result is not None
    assert result.score == Decimal("75")
    assert result.scores.fundamental == Decimal("80")
    assert result.scores.risk == Decimal("60")
    assert "基本面" in result.fundamentals


def test_analyze_stream_handles_unparseable(db_session, monkeypatch):
    """Garbage LLM output -> error event + (done, None)."""
    _patch_llm(monkeypatch, ["not json at all"])
    events = list(stock_agent.analyze_stream(db_session, "600519"))

    types = [e[0] for e in events]
    assert "error" in types
    assert events[-1] == ("done", None)


def test_persist_and_get_latest(db_session, monkeypatch):
    """persist_result writes a row that get_latest can read back; cleanup runs."""
    _patch_llm(monkeypatch, [_FAKE_JSON])
    events = list(stock_agent.analyze_stream(db_session, "600519"))
    result = events[-1][1]
    result.request_id = "an-test-persist"

    try:
        row = analysis_service.persist_result(
            db_session, "an-test-persist", user_id=None, result=result
        )
        assert row.id is not None

        fetched = analysis_service.get_latest(db_session, "600519")
        assert fetched is not None
        assert fetched.request_id == "an-test-persist"
        assert fetched.score == Decimal("75.00")
    finally:
        # CLEANUP: never leave test rows in the prod DB.
        db_session.query(SaAiAnalysis).filter_by(request_id="an-test-persist").delete()
        db_session.commit()


def test_rate_limit_blocks_within_cooldown():
    """A second call for the same code inside the cooldown raises 429."""
    # Use a unique code so this test is order-independent.
    code = "TESTCODE_RATELIMIT_1"
    analysis_service._cooldown.pop(code, None)

    analysis_service._rate_limit(code)  # first call: ok
    with pytest.raises(HTTPException) as exc:
        analysis_service._rate_limit(code)  # second: blocked
    assert exc.value.status_code == 429

    # cleanup so the entry doesn't bleed into other tests.
    analysis_service._cooldown.pop(code, None)
