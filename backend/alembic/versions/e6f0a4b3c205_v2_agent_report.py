"""v2 agent report table

Creates ``sa_agent_report`` for the 4 V2 agents (BP-V2-009~012).

Revision ID: e6f0a4b3c205
Revises: d5e9f3a2b104
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = 'e6f0a4b3c205'
down_revision: Union[str, Sequence[str], None] = 'd5e9f3a2b104'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sa_agent_report',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('agent', sa.String(length=20), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=True),
        sa.Column('target', sa.String(length=50), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('scores', mysql.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_index('idx_agent_date', 'sa_agent_report', ['agent', 'trade_date'], unique=False)
    op.create_index('idx_target', 'sa_agent_report', ['target'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_target', table_name='sa_agent_report')
    op.drop_index('idx_agent_date', table_name='sa_agent_report')
    op.drop_table('sa_agent_report')
