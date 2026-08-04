"""Tests for the RAG knowledge service + RAG chain (V2 阶段 L, BP-V2-006).

ALL embedding / LLM access is mocked:

* ``embeddings.embed_texts`` / ``embed_query`` are monkeypatched to return
  handcrafted vectors so the cosine ranking is fully deterministic — no real
  embedding API key required.
* ``llm_client.chat`` is patched so :func:`app.ai.rag.answer_with_rag` runs
  end-to-end without network.

The DB is the real ``stock_analysis`` DB; every test cleans up the docs /
chunks it creates so the suite stays idempotent.
"""

from datetime import date

import pytest

from app.ai import embeddings, llm_client, rag
from app.models.knowledge import SaKnowledgeChunk, SaKnowledgeDoc
from app.services import knowledge_service


# --------------------------------------------------------------------------- #
# 辅助：mock embedding + 清理
# --------------------------------------------------------------------------- #


def _fake_embed(monkeypatch, mapping: dict, default=None):
    """让 ``embed_query`` / ``embed_texts`` 按 ``mapping`` 返回预设向量。

    ``mapping``: ``{文本子串 -> 向量}``，命中子串即返回该向量；未命中走
    ``default``（默认零向量），保证维度一致。
    """

    def _vec_for(text: str) -> list[float]:
        for key, vec in mapping.items():
            if key in text:
                return vec
        return list(default) if default is not None else [0.0, 0.0, 0.0]

    def _embed_query(text: str) -> list[float]:
        return _vec_for(text)

    def _embed_texts(texts: list[str]) -> list[list[float]]:
        return [_vec_for(t) for t in texts]

    monkeypatch.setattr(embeddings, "embed_query", _embed_query)
    monkeypatch.setattr(embeddings, "embed_texts", _embed_texts)


def _cleanup_doc(db_session, doc_id: int) -> None:
    """删除一篇文档及其所有分块，避免污染线上数据。"""
    db_session.query(SaKnowledgeChunk).filter_by(doc_id=doc_id).delete()
    db_session.query(SaKnowledgeDoc).filter_by(id=doc_id).delete()
    db_session.commit()


# --------------------------------------------------------------------------- #
# split_text 纯函数
# --------------------------------------------------------------------------- #


def test_split_text_short_paragraphs():
    """短段落各自成块；空行被丢弃。"""
    parts = knowledge_service.split_text("第一段。\n\n第二段。\n\n")
    assert parts == ["第一段。", "第二段。"]


def test_split_text_long_paragraph_sliding_window():
    """超长段落按 500 字滑窗、50 字重叠切分。"""
    long = "字" * 1100
    parts = knowledge_service.split_text(long)
    assert len(parts) >= 3
    assert all(len(p) <= knowledge_service.CHUNK_SIZE for p in parts)
    # 前两块应有 CHUNK_OVERLAP 字符的重叠。
    overlap = knowledge_service.CHUNK_OVERLAP
    assert parts[0][-overlap:] == parts[1][:overlap]


def test_split_text_empty():
    assert knowledge_service.split_text("") == []


# --------------------------------------------------------------------------- #
# ingest_doc
# --------------------------------------------------------------------------- #


def test_ingest_doc_chunks_and_persists(db_session, monkeypatch):
    """ingest_doc 按段落分块、落库并把 status 置为 embedded。"""
    _fake_embed(monkeypatch, {"茅台": [1.0, 0.0, 0.0]})

    content = "茅台是白酒龙头。\n\n2023 年营收 1500 亿。\n\n毛利率超 90%。"
    doc = knowledge_service.ingest_doc(
        db_session,
        title="茅台研报-test",
        content=content,
        source="测试",
        stock_code="600519",
        doc_date=date(2024, 1, 1),
    )
    try:
        assert doc.status == "embedded"
        assert doc.id is not None

        chunks = (
            db_session.query(SaKnowledgeChunk)
            .filter_by(doc_id=doc.id)
            .order_by(SaKnowledgeChunk.chunk_index.asc())
            .all()
        )
        # 三个短段落 -> 三块。
        assert len(chunks) == 3
        assert [c.chunk_index for c in chunks] == [0, 1, 2]
        # 每块都写了 embedding（list[float]）。
        assert all(c.embedding is not None for c in chunks)
        assert chunks[0].embedding == [1.0, 0.0, 0.0]
        assert chunks[0].text == "茅台是白酒龙头。"
    finally:
        _cleanup_doc(db_session, doc.id)


def test_ingest_doc_marks_failed_on_embed_error(db_session, monkeypatch):
    """embedding API 报错时文档标记 failed 并抛 RuntimeError。"""

    def _boom(_texts):
        raise RuntimeError("embed api down")

    monkeypatch.setattr(embeddings, "embed_texts", _boom)

    with pytest.raises(RuntimeError):
        knowledge_service.ingest_doc(
            db_session, title="失败研报-test", content="一段内容"
        )

    failed = (
        db_session.query(SaKnowledgeDoc)
        .filter_by(title="失败研报-test", status="failed")
        .all()
    )
    try:
        assert len(failed) == 1
        # 失败时不应残留 chunk。
        assert (
            db_session.query(SaKnowledgeChunk)
            .filter_by(doc_id=failed[0].id)
            .count()
            == 0
        )
    finally:
        for d in failed:
            _cleanup_doc(db_session, d.id)


def test_ingest_doc_empty_content_skips_embed(db_session, monkeypatch):
    """空内容直接标记 embedded，不调用 embedding。"""
    called = {"n": 0}

    def _embed_texts(_texts):
        called["n"] += 1
        return []

    monkeypatch.setattr(embeddings, "embed_texts", _embed_texts)

    doc = knowledge_service.ingest_doc(db_session, title="空文档-test", content="")
    try:
        assert doc.status == "embedded"
        assert called["n"] == 0  # 没有触发 embedding 调用
    finally:
        _cleanup_doc(db_session, doc.id)


# --------------------------------------------------------------------------- #
# search（cosine 排序）
# --------------------------------------------------------------------------- #


def test_search_ranks_by_cosine(db_session, monkeypatch):
    """search 按 cosine 相似度降序返回 Top-K，并带来源标注。"""
    # 三个向量：茅台沿 x 轴，五粮液沿 y 轴，无关项零向量。查询"茅台"会与第一块最相似。
    _fake_embed(
        monkeypatch,
        {
            "茅台": [1.0, 0.0, 0.0],
            "五粮液": [0.0, 1.0, 0.0],
            "无关": [0.0, 0.0, 0.0],
        },
        default=[0.0, 0.0, 0.0],
    )

    doc1 = knowledge_service.ingest_doc(
        db_session, title="茅台研报-test", content="茅台是白酒龙头。", stock_code="600519"
    )
    doc2 = knowledge_service.ingest_doc(
        db_session, title="五粮液研报-test", content="五粮液是浓香龙头。", stock_code="000858"
    )
    doc3 = knowledge_service.ingest_doc(
        db_session, title="无关文档-test", content="无关内容。"
    )
    try:
        hits = knowledge_service.search(db_session, "茅台", top_k=3)
        assert len(hits) == 3
        # 最相似的应是茅台那一块（cosine = 1.0）。
        assert hits[0]["title"] == "茅台研报-test"
        assert hits[0]["score"] == pytest.approx(1.0)
        assert hits[0]["text"] == "茅台是白酒龙头。"
        assert hits[0]["stock_code"] == "600519"
        # 五粮液块与"茅台"查询正交（cosine=0）。
        wly = next(h for h in hits if h["title"] == "五粮液研报-test")
        assert wly["score"] == pytest.approx(0.0)
    finally:
        for d in (doc1, doc2, doc3):
            _cleanup_doc(db_session, d.id)


def test_search_filters_by_stock_code(db_session, monkeypatch):
    """按 stock_code 过滤时只返回该股票的文档块。"""
    _fake_embed(monkeypatch, {"a": [1.0, 0.0]}, default=[1.0, 0.0])

    doc1 = knowledge_service.ingest_doc(
        db_session, title="doc-a-test", content="aaa", stock_code="600519"
    )
    doc2 = knowledge_service.ingest_doc(
        db_session, title="doc-b-test", content="bbb", stock_code="000858"
    )
    try:
        hits = knowledge_service.search(db_session, "aaa", top_k=5, stock_code="600519")
        assert len(hits) == 1
        assert hits[0]["title"] == "doc-a-test"
    finally:
        _cleanup_doc(db_session, doc1.id)
        _cleanup_doc(db_session, doc2.id)


def test_search_empty_query_returns_empty(db_session):
    assert knowledge_service.search(db_session, "", top_k=5) == []
    assert knowledge_service.search(db_session, "x", top_k=0) == []


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #


def test_list_and_delete_doc(db_session, monkeypatch):
    _fake_embed(monkeypatch, {"x": [1.0, 0.0]})
    doc = knowledge_service.ingest_doc(
        db_session, title="删除用-test", content="xxx", stock_code="600519"
    )
    try:
        listed = knowledge_service.list_docs(db_session, stock_code="600519")
        assert any(d.id == doc.id for d in listed)

        fetched = knowledge_service.get_doc(db_session, doc.id)
        assert fetched is not None
        assert fetched.title == "删除用-test"
    finally:
        assert knowledge_service.delete_doc(db_session, doc.id) is True
        # 删除后 chunk 也应消失。
        assert (
            db_session.query(SaKnowledgeChunk).filter_by(doc_id=doc.id).count() == 0
        )
        assert knowledge_service.get_doc(db_session, doc.id) is None
        # 再删一次返回 False。
        assert knowledge_service.delete_doc(db_session, doc.id) is False


# --------------------------------------------------------------------------- #
# answer_with_rag
# --------------------------------------------------------------------------- #


def test_answer_with_rag_returns_answer_and_sources(db_session, monkeypatch):
    """RAG 命中时返回 LLM 回答 + 去重后的来源标注。"""
    _fake_embed(monkeypatch, {"茅台": [1.0, 0.0, 0.0]})
    doc = knowledge_service.ingest_doc(
        db_session,
        title="茅台研报-test",
        content="茅台是白酒龙头。",
        source="测试来源",
        stock_code="600519",
    )
    # LLM 返回固定文本（不调真实 API）。
    monkeypatch.setattr(llm_client, "chat", lambda messages: "茅台是 A 股白酒龙头。")
    try:
        answer, sources = rag.answer_with_rag(db_session, "茅台", score_threshold=0.5)
        assert "茅台" in answer
        assert len(sources) == 1
        assert sources[0]["doc_id"] == doc.id
        assert sources[0]["title"] == "茅台研报-test"
        assert sources[0]["source"] == "测试来源"
    finally:
        _cleanup_doc(db_session, doc.id)


def test_answer_with_rag_no_hit_returns_empty(db_session, monkeypatch):
    """知识库无相关命中时返回空回答、空来源（调用方应回退到普通对话）。"""
    _fake_embed(monkeypatch, {"茅台": [1.0, 0.0, 0.0]}, default=[0.0, 1.0])
    doc = knowledge_service.ingest_doc(
        db_session, title="茅台研报-test", content="茅台是白酒龙头。"
    )
    # chat 不应被调用（无命中即短路）。
    monkeypatch.setattr(
        llm_client, "chat", lambda messages: pytest.fail("LLM should not be called")
    )
    try:
        # 查询与文档正交（cosine=0），低于阈值 -> 视为无命中。
        answer, sources = rag.answer_with_rag(
            db_session, "完全不相关的问题xyz", score_threshold=0.5
        )
        assert answer == ""
        assert sources == []
    finally:
        _cleanup_doc(db_session, doc.id)


def test_answer_with_rag_dedupes_sources(db_session, monkeypatch):
    """同一文档的多个命中块只产生一条来源标注。"""
    _fake_embed(monkeypatch, {"茅台": [1.0, 0.0, 0.0]})
    doc = knowledge_service.ingest_doc(
        db_session,
        title="茅台研报-test",
        content="茅台是白酒龙头。\n\n茅台毛利率超 90%。",
        stock_code="600519",
    )
    monkeypatch.setattr(llm_client, "chat", lambda messages: "回答")
    try:
        answer, sources = rag.answer_with_rag(db_session, "茅台", score_threshold=0.5)
        assert answer == "回答"
        # 两块命中但只来自一个文档 -> 一条来源。
        assert len(sources) == 1
        assert sources[0]["doc_id"] == doc.id
    finally:
        _cleanup_doc(db_session, doc.id)
