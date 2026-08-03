"""文本向量化客户端（V2 阶段 L）。

封装 LangChain 的 ``OpenAIEmbeddings``，对接 OpenAI 兼容的 embedding
端点（DeepSeek / 通义 / OpenAI 等）。所有网络访问都经过本模块，因此测试
可 monkeypatch ``get_embedder`` / ``embed_texts`` / ``embed_query`` 以
脱离真实 API key 运行。

向量维度由所选模型决定（DeepSeek/OpenAI 常见为 1536），落库时按 list
存入 ``JSON`` 列——MySQL 8.4 暂不支持 ``VECTOR`` 类型，故采用 JSON 列 +
Python cosine 相似度检索的降级方案。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Iterable

from langchain_openai import OpenAIEmbeddings

from app.core.config import settings


@lru_cache(maxsize=1)
def get_embedder() -> OpenAIEmbeddings:
    """返回单例 ``OpenAIEmbeddings`` 客户端。

    ``model`` 默认走 ``text-embedding-v1``（DeepSeek/OpenAI 兼容的通用名），
    可通过环境变量 ``EMBEDDING_MODEL`` 覆盖。``base_url`` / ``api_key`` 复用
    LLM 的设置，避免再引入一组配置项。
    """
    model = getattr(settings, "embedding_model", None) or "text-embedding-v1"
    return OpenAIEmbeddings(
        model=model,
        api_key=settings.llm_api_key,
        base_url=getattr(settings, "embedding_base_url", None) or settings.llm_base_url,
    )


def embed_texts(texts: list[str]) -> list[list[float]]:
    """对一组文本批量计算 embedding。

    Args:
        texts: 待向量化的文本列表（非空字符串）。

    Returns:
        与 ``texts`` 等长的 ``list[float]`` 列表，顺序一一对应。
    """
    if not texts:
        return []
    embedder = get_embedder()
    vectors = embedder.embed_documents(list(texts))
    return [list(v) for v in vectors]


def embed_query(text: str) -> list[float]:
    """对单条查询文本计算 embedding（与文档块共享同一模型）。"""
    embedder = get_embedder()
    return list(embedder.embed_query(text))


def cosine_similarity(vec_a: Iterable[float], vec_b: Iterable[float]) -> float:
    """计算两个向量的余弦相似度（numpy 实现，无第三方向量库依赖）。

    任一向量为零向量时返回 ``0.0``，避免除零；用于 ``knowledge_service``
    在内存中对所有 chunk 做相似度排序。
    """
    import numpy as np

    a = np.fromiter(vec_a, dtype=float)
    b = np.fromiter(vec_b, dtype=float)
    # 长度不一致时截断到较短一侧（防御性，正常情况下两者同维）。
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))
