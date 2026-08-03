"""RAG 知识库服务（V2 阶段 L, BP-V2-006）。

职责：

1. **文档入库** (:func:`ingest_doc`)：先落 ``sa_knowledge_doc`` (status=
   ``pending``)，按段落分块（每块约 500 字，超长段落按 500 字滑窗、重叠
   50 字）→ 调 embedding API → 批量写 ``sa_knowledge_chunk``，文档置为
   ``embedded``；失败置 ``failed``。
2. **检索** (:func:`search`)：embed 查询 → 遍历所有 chunk 在内存里算
   cosine 相似度（MySQL 8.4 暂不支持 VECTOR 类型，降级方案）→ 返回 Top-K，
   附带来源文档标题 / 来源 / 股票代码。
3. **CRUD**：``list_docs`` / ``get_doc`` / ``delete_doc``（级联删除 chunk）。

所有 embedding 调用经 :mod:`app.ai.embeddings`，因此测试 monkeypatch 它
即可脱离真实 API key。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import embeddings
from app.models.knowledge import SaKnowledgeChunk, SaKnowledgeDoc

logger = logging.getLogger(__name__)

# 分块参数：目标块大小（字符数）、滑窗步长（块大小 - 重叠）、重叠量。
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


# --------------------------------------------------------------------------- #
# 文档 CRUD
# --------------------------------------------------------------------------- #


def list_docs(
    db: Session,
    stock_code: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[SaKnowledgeDoc]:
    """列出知识库文档，按创建时间倒序。可按 ``stock_code`` / ``status`` 过滤。"""
    stmt = select(SaKnowledgeDoc)
    if stock_code:
        stmt = stmt.where(SaKnowledgeDoc.stock_code == stock_code)
    if status:
        stmt = stmt.where(SaKnowledgeDoc.status == status)
    stmt = stmt.order_by(SaKnowledgeDoc.created_at.desc()).limit(limit)
    return list(db.execute(stmt).scalars().all())


def get_doc(db: Session, doc_id: int) -> SaKnowledgeDoc | None:
    """按主键取单篇文档。"""
    return db.get(SaKnowledgeDoc, doc_id)


def delete_doc(db: Session, doc_id: int) -> bool:
    """删除文档及其所有分块（chunk 无独立外键级联，故显式删除）。

    Returns:
        ``True`` 表示删到了文档，``False`` 表示文档不存在。
    """
    doc = db.get(SaKnowledgeDoc, doc_id)
    if doc is None:
        return False
    db.execute(
        SaKnowledgeChunk.__table__.delete().where(SaKnowledgeChunk.doc_id == doc_id)
    )
    db.delete(doc)
    db.commit()
    return True


# --------------------------------------------------------------------------- #
# 文档入库（分块 + 向量化）
# --------------------------------------------------------------------------- #


def split_text(content: str) -> list[str]:
    """把文档正文切分为约 500 字的文本块。

    策略：先按换行切成段落；短段落直接作为一块；超长段落（> CHUNK_SIZE）
    以 CHUNK_SIZE 字符为窗口、CHUNK_OVERLAP 字符为重叠滑窗切分，保证块间
    上下文连续。空块（纯空白）被丢弃。
    """
    if not content:
        return []

    chunks: list[str] = []
    for para in content.splitlines():
        para = para.strip()
        if not para:
            continue
        if len(para) <= CHUNK_SIZE:
            chunks.append(para)
            continue
        # 超长段落：滑窗切分。
        step = CHUNK_SIZE - CHUNK_OVERLAP
        i = 0
        while i < len(para):
            chunks.append(para[i : i + CHUNK_SIZE])
            i += step
    return chunks


def ingest_doc(
    db: Session,
    title: str,
    content: str,
    source: str | None = None,
    stock_code: str | None = None,
    doc_date: Any | None = None,
) -> SaKnowledgeDoc:
    """入库一篇文档：落库 → 分块 → 向量化 → 写 chunk → 置为 ``embedded``。

    Args:
        db: 调用方管理的 SQLAlchemy session。
        title: 文档标题。
        content: 文档正文（任意长度）。
        source: 来源（如 "机构研报" / "巨潮资讯"）。
        stock_code: 关联股票代码（可选）。
        doc_date: 文档日期（可选，``date`` 或字符串）。

    Returns:
        已刷新的 :class:`SaKnowledgeDoc`（``status`` 为 ``embedded`` 或
        ``failed``）。
    """
    doc = SaKnowledgeDoc(
        title=title,
        content=content,
        source=source,
        stock_code=stock_code,
        doc_date=doc_date,
        status="pending",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    chunks = split_text(content)
    if not chunks:
        # 没有可向量化的文本，直接标记为 embedded（空内容也算成功入库）。
        doc.status = "embedded"
        db.commit()
        db.refresh(doc)
        return doc

    try:
        vectors = embeddings.embed_texts(chunks)
    except Exception as e:  # noqa: BLE001 - embedding 失败要把文档标记 failed。
        logger.exception("embed texts failed for doc %s", doc.id)
        doc.status = "failed"
        db.commit()
        db.refresh(doc)
        raise RuntimeError(f"embedding failed: {e}") from e

    rows = [
        SaKnowledgeChunk(
            doc_id=doc.id,
            chunk_index=idx,
            text=text,
            embedding=(vectors[idx] if idx < len(vectors) else None),
        )
        for idx, text in enumerate(chunks)
    ]
    db.add_all(rows)
    doc.status = "embedded"
    db.commit()
    db.refresh(doc)
    return doc


# --------------------------------------------------------------------------- #
# 向量检索
# --------------------------------------------------------------------------- #


def search(
    db: Session,
    query: str,
    top_k: int = 5,
    stock_code: str | None = None,
) -> list[dict]:
    """检索与 ``query`` 最相关的 Top-K 文本块。

    流程：embed 查询 → 拉取所有已向量化的 chunk（可按 ``stock_code`` 过滤）
    → 内存里算 cosine 相似度 → 取 Top-K。每个结果携带来源文档的标题 /
    来源 / 股票代码，方便上层做来源标注。

    Args:
        db: 调用方管理的 SQLAlchemy session。
        query: 用户查询文本。
        top_k: 返回结果数上限。
        stock_code: 可选，仅在该股票的文档范围内检索。

    Returns:
        ``[{doc_id, title, source, stock_code, text, score, chunk_index}]``，
        按 ``score`` 降序。
    """
    if not query or top_k <= 0:
        return []

    query_vec = embeddings.embed_query(query)

    stmt = (
        select(SaKnowledgeChunk, SaKnowledgeDoc)
        .join(SaKnowledgeDoc, SaKnowledgeChunk.doc_id == SaKnowledgeDoc.id)
        .where(SaKnowledgeChunk.embedding.is_not(None))
    )
    if stock_code:
        stmt = stmt.where(SaKnowledgeDoc.stock_code == stock_code)

    scored: list[dict] = []
    for chunk, doc in db.execute(stmt).all():
        score = embeddings.cosine_similarity(query_vec, chunk.embedding or [])
        scored.append(
            {
                "doc_id": doc.id,
                "title": doc.title,
                "source": doc.source,
                "stock_code": doc.stock_code,
                "text": chunk.text,
                "chunk_index": chunk.chunk_index,
                "score": score,
            }
        )

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]
