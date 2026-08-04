"""ORM mappings for the RAG knowledge base (V2 阶段 L, BP-V2-006).

Contains:

* :class:`SaKnowledgeDoc`  - 一篇上传的知识库文档（研报 / 公告 / 财报等）。
  ``content`` 用 MEDIUMTEXT 容纳长文，``status`` 跟踪向量化进度
  (``pending`` -> ``embedded`` / ``failed``)。
* :class:`SaKnowledgeChunk` - 文档被切分后的一个文本块及其 embedding
  (JSON 列)。MySQL 8.4 暂不支持 VECTOR 类型，因此采用 JSON 列 + Python
  cosine 相似度检索的降级方案（详见 :mod:`app.services.knowledge_service`）。
"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.mysql import JSON, MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SaKnowledgeDoc(Base):
    """一篇知识库文档。

    ``status`` 取值：

    * ``pending``  - 刚创建，尚未向量化。
    * ``embedded`` - 已分块并写入向量，可被检索。
    * ``failed``   - 向量化过程失败（如 embedding API 报错）。
    """

    __tablename__ = "sa_knowledge_doc"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String(100))
    stock_code: Mapped[Optional[str]] = mapped_column(String(10))
    doc_date: Mapped[Optional[date]] = mapped_column(Date)
    content: Mapped[str] = mapped_column(MEDIUMTEXT, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_status_created", "status", "created_at"),
        Index("idx_stock_code", "stock_code"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaKnowledgeDoc(id={self.id!r}, title={self.title!r}, "
            f"status={self.status!r})"
        )


class SaKnowledgeChunk(Base):
    """文档的一个文本块及其 embedding 向量。

    ``embedding`` 以 JSON 数组存储（如 ``[0.012, -0.034, ...]``），降级方案
    下由 Python 在内存中计算 cosine 相似度，而非依赖数据库原生向量检索。
    ``(doc_id, chunk_index)`` 唯一标识一篇文档内的一个块。
    """

    __tablename__ = "sa_knowledge_chunk"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    doc_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sa_knowledge_doc.id"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Optional[list]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_doc_id", "doc_id"),
        Index("uk_doc_chunk", "doc_id", "chunk_index", unique=True),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaKnowledgeChunk(id={self.id!r}, doc_id={self.doc_id!r}, "
            f"chunk_index={self.chunk_index!r})"
        )
