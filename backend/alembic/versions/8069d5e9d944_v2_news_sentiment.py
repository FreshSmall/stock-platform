"""v2 news sentiment table

阶段 N1（BP-V2-008）新闻情绪采集：创建 ``sa_news_sentiment`` 表，存放
AkShare 抓取的财联社/东方财富实时资讯，以及由 LLM 打出的情绪分（-1~+1）、
摘要、关联个股与板块。

手写迁移（参照 ``a34015da0db5`` / ``b7c2f9a1d3e5`` 的格式）：只 CREATE 一张
``sa_`` 表，不去碰任何只读外部表，沿用本仓库迁移的一贯约定。

注：``down_revision`` 接在 ``e6f0a4b3c205``（agent_report，与本文档并行落库的
V2 阶段 M 表）之后，避免出现分叉 head —— 两条迁移原本都从 ``d5e9f3a2b104``
分叉，这里串成单链，``alembic upgrade head`` 可一次性把两张表都建好。

Revision ID: 8069d5e9d944
Revises: e6f0a4b3c205
Create Date: 2026-08-04 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = '8069d5e9d944'
down_revision: Union[str, Sequence[str], None] = 'e6f0a4b3c205'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sa_news_sentiment',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('pub_time', sa.DateTime(), nullable=True, comment='资讯发布时间'),
        sa.Column('title', sa.String(length=300), nullable=True, comment='标题'),
        sa.Column('content', sa.Text(), nullable=True, comment='正文/摘要内容'),
        sa.Column('source', sa.String(length=50), nullable=True, comment='来源（cls/em/em-stock）'),
        sa.Column('stock_codes', mysql.JSON(), nullable=True, comment='关联个股代码列表'),
        sa.Column('sector', sa.String(length=50), nullable=True, comment='关联板块'),
        sa.Column('sentiment', sa.Numeric(precision=4, scale=3), nullable=True, comment='情绪分 -1~+1'),
        sa.Column('summary', sa.String(length=500), nullable=True, comment='LLM 摘要'),
        sa.PrimaryKeyConstraint('id'),
        # 幂等 UPSERT 的依据：同一来源 + 标题 + 发布时间视为同一条资讯。
        # ``title`` 允许 NULL（财联社部分电报无标题，靠 content 兜底），
        # MySQL 的唯一索引允许多个 NULL 共存，不会误判。
        sa.UniqueConstraint('source', 'title', 'pub_time', name='uk_source_title_time'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    # 按发布时间倒序翻页查询（API 的 /news 列表主路径）。
    op.create_index(
        'idx_pub_time', 'sa_news_sentiment', ['pub_time'], unique=False,
    )
    # 按板块过滤（list_news 的 sector 过滤分支）。
    op.create_index(
        'idx_sector_pub', 'sa_news_sentiment', ['sector', 'pub_time'], unique=False,
    )


def downgrade() -> None:
    op.drop_index('idx_sector_pub', table_name='sa_news_sentiment')
    op.drop_index('idx_pub_time', table_name='sa_news_sentiment')
    op.drop_table('sa_news_sentiment')
