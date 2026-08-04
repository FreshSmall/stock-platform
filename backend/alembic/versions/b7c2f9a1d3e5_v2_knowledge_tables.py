"""v2 knowledge base tables

阶段 L（BP-V2-006）RAG 知识库：创建两张 ``sa_`` 表：

* ``sa_knowledge_doc``   — 知识库文档（研报 / 公告 / 财报等）。
* ``sa_knowledge_chunk`` — 文档分块及其 embedding（JSON 列，MySQL 8.4 暂
  不支持 VECTOR 类型的降级方案）。

Hand-edited from autogenerate output: removed all spurious drop/alter noise
against the read-only external tables, keeping only the two CREATEs — same
convention as the previous ``sa_`` migrations.

Revision ID: b7c2f9a1d3e5
Revises: a311648aed49
Create Date: 2026-08-03 23:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = 'b7c2f9a1d3e5'
down_revision: Union[str, Sequence[str], None] = 'a311648aed49'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sa_knowledge_doc',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('source', sa.String(length=100), nullable=True),
        sa.Column('stock_code', sa.String(length=10), nullable=True),
        sa.Column('doc_date', sa.Date(), nullable=True),
        sa.Column('content', mysql.MEDIUMTEXT(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_index('idx_status_created', 'sa_knowledge_doc', ['status', 'created_at'], unique=False)
    op.create_index('idx_stock_code', 'sa_knowledge_doc', ['stock_code'], unique=False)

    op.create_table(
        'sa_knowledge_chunk',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('doc_id', sa.BigInteger(), nullable=False),
        sa.Column('chunk_index', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('embedding', mysql.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['doc_id'], ['sa_knowledge_doc.id']),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_index('idx_doc_id', 'sa_knowledge_chunk', ['doc_id'], unique=False)
    op.create_index('uk_doc_chunk', 'sa_knowledge_chunk', ['doc_id', 'chunk_index'], unique=True)


def downgrade() -> None:
    op.drop_index('uk_doc_chunk', table_name='sa_knowledge_chunk')
    op.drop_index('idx_doc_id', table_name='sa_knowledge_chunk')
    op.drop_table('sa_knowledge_chunk')
    op.drop_index('idx_stock_code', table_name='sa_knowledge_doc')
    op.drop_index('idx_status_created', table_name='sa_knowledge_doc')
    op.drop_table('sa_knowledge_doc')
