"""RAG 问答链（V2 阶段 L, BP-V2-006）。

:func:`answer_with_rag` 实现「检索 → 拼上下文 → 调 LLM → 标注来源」的
完整链路：

1. 用 :func:`app.services.knowledge_service.search` 取与 query 最相关的
   Top-K 文本块。
2. 把检索到的片段拼成上下文，连同用户问题一起送给 LLM（经
   :mod:`app.ai.llm_client`，便于测试 monkeypatch）。
3. 返回 ``(answer, sources)``：``answer`` 是 LLM 的文本回答，``sources``
   是被引用文档的来源标注（去重后的 ``[{doc_id, title, source}]``）。

当知识库无命中时返回 ``("", [])``，由调用方决定是否走普通对话。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.orm import Session

from app.ai import llm_client
from app.services import knowledge_service

logger = logging.getLogger(__name__)

# 检索 Top-K 个文本块拼上下文；过大撑爆 prompt，过小召回不足。
RAG_TOP_K = 5
# 相关性阈值：cosine 相似度低于此值的块视为无关，不进入上下文。
RAG_SCORE_THRESHOLD = 0.3

RAG_SYSTEM_PROMPT = """你是专业的 A 股投研助理。请根据下方「知识库资料」回答用户问题。
规则：
1. 优先使用资料中的事实，不要编造未在资料中出现的信息。
2. 若资料不足以回答，明确说明"知识库暂无相关信息"。
3. 回答使用简洁的中文 markdown。
4. 涉及具体投资标的时，附上风险提示。"""


def _build_context(hits: list[dict]) -> str:
    """把检索到的文本块拼成 prompt 中的「知识库资料」段落。

    每块前标注序号与来源标题，便于 LLM 引用；评分仅用于内部排序，不暴露
    给模型以免干扰输出。
    """
    if not hits:
        return "（知识库暂无相关资料）"
    parts: list[str] = []
    for idx, hit in enumerate(hits, start=1):
        title = hit.get("title") or "未命名"
        parts.append(f"[{idx}] 来源：{title}\n{hit.get('text', '')}")
    return "\n\n".join(parts)


def _dedupe_sources(hits: list[dict]) -> list[dict]:
    """从命中的块里去重提取来源标注（同一文档只出现一次）。"""
    seen: set[int] = set()
    sources: list[dict] = []
    for hit in hits:
        doc_id = hit.get("doc_id")
        if doc_id is None or doc_id in seen:
            continue
        seen.add(doc_id)
        sources.append(
            {
                "doc_id": doc_id,
                "title": hit.get("title"),
                "source": hit.get("source"),
                "stock_code": hit.get("stock_code"),
            }
        )
    return sources


def answer_with_rag(
    db: Session,
    query: str,
    session: Any | None = None,
    top_k: int = RAG_TOP_K,
    score_threshold: float = RAG_SCORE_THRESHOLD,
) -> tuple[str, list[dict]]:
    """对 ``query`` 走 RAG 问答，返回 ``(answer, sources)``。

    Args:
        db: 调用方管理的 SQLAlchemy session。
        query: 用户问题。
        session: 预留参数，便于将来接入多轮上下文（当前未使用）。
        top_k: 检索文本块数量上限。
        score_threshold: cosine 相似度阈值，低于此值的命中被丢弃。

    Returns:
        ``(answer, sources)``。``answer`` 为空字符串表示知识库无命中（调用
        方可回退到普通对话）；``sources`` 为去重后的来源文档列表。
    """
    hits = knowledge_service.search(db, query, top_k=top_k)
    hits = [h for h in hits if h.get("score", 0.0) >= score_threshold]
    if not hits:
        return "", []

    context = _build_context(hits)
    user_prompt = f"""【知识库资料】
{context}

【用户问题】
{query}

请基于上述资料作答。"""

    messages = [
        SystemMessage(content=RAG_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    try:
        answer = llm_client.chat(messages)
    except Exception as e:  # noqa: BLE001 - LLM 调用失败不阻断检索结果。
        logger.exception("rag llm chat failed")
        raise RuntimeError(f"rag llm chat failed: {e}") from e

    sources = _dedupe_sources(hits)
    return answer, sources
