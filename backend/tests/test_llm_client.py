"""Tests for app.ai.llm_client and app.ai.prompts.

ALL LLM access is mocked — no real network calls are made. The fake LLM
yields predetermined chunks so we can assert on streaming/one-shot behaviour
without an API key or connectivity.
"""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.ai import llm_client, prompts


class _FakeChunk:
    """Mimics a LangChain streaming chunk: only ``.content`` is read."""

    def __init__(self, content):
        self.content = content


class _FakeLLM:
    """Fake LLM that yields predetermined chunks."""

    def __init__(self, chunks):
        self._chunks = chunks

    def invoke(self, messages):
        return type("Resp", (), {"content": "".join(self._chunks)})()

    def stream(self, messages):
        for c in self._chunks:
            yield _FakeChunk(c)


def test_chat_returns_full_text(monkeypatch):
    """chat() joins streamed chunks into the full text response."""
    monkeypatch.setattr(llm_client, "get_llm", lambda: _FakeLLM(["Hello ", "world"]))
    assert llm_client.chat([]) == "Hello world"


def test_stream_chat_yields_chunks(monkeypatch):
    """stream_chat() yields each non-empty content chunk in order."""
    monkeypatch.setattr(llm_client, "get_llm", lambda: _FakeLLM(["a", "b", "c"]))
    chunks = list(llm_client.stream_chat([]))
    assert chunks == ["a", "b", "c"]


def test_to_messages_maps_roles():
    """to_messages() maps known roles and defaults unknown roles to Human."""
    msgs = llm_client.to_messages([
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
        {"role": "unknown", "content": "x"},  # defaults to Human
    ])
    assert isinstance(msgs[0], SystemMessage)
    assert isinstance(msgs[1], HumanMessage)
    assert isinstance(msgs[2], AIMessage)
    assert isinstance(msgs[3], HumanMessage)


def test_build_analysis_user_prompt_contains_stock_code():
    """The user prompt embeds stock code/name and the provided data points."""
    p = prompts.build_analysis_user_prompt(
        "600519",
        "贵州茅台",
        context={
            "kline_recent": [
                {"date": "2026-07-28", "close": 1320, "pct_change": 2.36, "volume": 5313457}
            ],
            "indicators": {"macd": {"dif": -30}},
            "finance": {"pe": 18.5},
            "money_flow": {"main_net_inflow": -1000000},
        },
    )
    assert "600519" in p
    assert "贵州茅台" in p
    assert "1320" in p
    assert "18.5" in p


def test_system_prompt_requires_json_output():
    """The system prompt instructs JSON output and defines the score schema."""
    assert "JSON" in prompts.SYSTEM_PROMPT
    assert "score" in prompts.SYSTEM_PROMPT


def test_risk_disclaimer_present():
    """The risk disclaimer states the output is not investment advice."""
    assert "不构成投资建议" in prompts.RISK_DISCLAIMER
