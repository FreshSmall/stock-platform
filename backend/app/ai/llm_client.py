"""LLM client wrapper.

Uses langchain_openai.ChatOpenAI against an OpenAI-compatible endpoint
(DeepSeek default). The API key/base_url/model come from settings. All network
calls go through this module so tests can monkeypatch it.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Iterable

from langchain_openai import ChatOpenAI
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)

from app.core.config import settings


@lru_cache(maxsize=1)
def get_llm() -> ChatOpenAI:
    """Return a singleton ChatOpenAI client configured from settings."""
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=0.3,
        streaming=True,
        timeout=60,
    )


def chat(messages: list[BaseMessage]) -> str:
    """Synchronous one-shot chat. Returns the full text response."""
    llm = get_llm()
    resp = llm.invoke(messages)
    return resp.content if hasattr(resp, "content") else str(resp)


def stream_chat(messages: list[BaseMessage]) -> Iterable[str]:
    """Streaming chat. Yields content chunks (strings) as they arrive."""
    llm = get_llm()
    for chunk in llm.stream(messages):
        # ChatOpenAI stream chunks expose .content
        content = chunk.content if hasattr(chunk, "content") else str(chunk)
        if content:
            yield content


def to_messages(history: list[dict]) -> list[BaseMessage]:
    """Convert [{role, content}] dicts to LangChain message objects."""
    mapping = {"system": SystemMessage, "user": HumanMessage, "assistant": AIMessage}
    out: list[BaseMessage] = []
    for m in history:
        cls = mapping.get(m.get("role", "user"), HumanMessage)
        out.append(cls(content=m.get("content", "")))
    return out
