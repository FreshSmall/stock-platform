"""ORM 映射：V2 阶段 N1 新闻情绪表 ``sa_news_sentiment``（BP-V2-008）。

该表由本服务写入：
* :class:`SaNewsSentiment` —— 每条资讯一行，存储原始信息（标题/正文/来源/
  发布时间）以及由 LLM 打出的情绪分（``-1 ~ +1``）、摘要、关联个股与板块。

数据来源是 AkShare 的财联社（``stock_info_global_cls``）/东方财富
（``stock_info_global_em`` / ``stock_news_em``）实时资讯，由
:mod:`app.data.sync_news` 采集；情绪打分由
:mod:`app.services.news_service` 通过 :mod:`app.ai.llm_client` 完成。
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.mysql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SaNewsSentiment(Base):
    """单条资讯及其情绪打分行。

    映射 ``sa_news_sentiment`` 表。``stock_codes`` 为 JSON 数组，存放该条资讯
    关联的 6 位个股代码（可能为空）；``sector`` 为关联板块名（可空）；
    ``sentiment`` 为 -1~+1 的情绪分（保留 3 位小数）。

    说明：``pub_time`` 直接用 DATETIME 存储（财联社给出的是 发布日期+发布时间
    两列，东财给出的是 'YYYY-MM-DD HH:MM:SS' 字符串），统一在采集层拼装为
    :class:`datetime.datetime` 后写入。
    """

    __tablename__ = "sa_news_sentiment"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pub_time: Mapped[Optional[datetime]] = mapped_column(DateTime, comment="资讯发布时间")
    title: Mapped[Optional[str]] = mapped_column(String(300), comment="标题")
    content: Mapped[Optional[str]] = mapped_column(Text, comment="正文/摘要内容")
    source: Mapped[Optional[str]] = mapped_column(String(50), comment="来源（cls/em/em-stock）")
    # JSON 数组，例如 ["600519","000858"]；无关联个股时为空数组或 NULL。
    stock_codes: Mapped[Optional[list]] = mapped_column(JSON, comment="关联个股代码列表")
    sector: Mapped[Optional[str]] = mapped_column(String(50), comment="关联板块")
    sentiment: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(4, 3), comment="情绪分 -1~+1"
    )
    summary: Mapped[Optional[str]] = mapped_column(String(500), comment="LLM 摘要")

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"SaNewsSentiment(id={self.id!r}, pub_time={self.pub_time!r}, "
            f"title={self.title!r}, sentiment={self.sentiment!r})"
        )
