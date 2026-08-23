"""v2 history back-fill state table

Creates ``sa_history_sync_state`` — per-stock progress of the 5-year daily-K
history back-fill (pending/done/failed + attempts), driven by the polling job
``history_backfill_tick``.

Revision ID: f2b7d9a4c6e8
Revises: 8069d5e9d944
Create Date: 2026-08-19 20:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = 'f2b7d9a4c6e8'
down_revision: Union[str, Sequence[str], None] = '8069d5e9d944'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sa_history_sync_state',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('stock_code', sa.String(length=10), nullable=False),
        sa.Column('target_start', sa.Date(), nullable=False),
        sa.Column('earliest_bar', sa.Date(), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False, server_default='pending'),
        sa.Column('attempts', sa.SmallInteger(), nullable=False, server_default='0'),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stock_code', name='uk_history_code'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_index('idx_history_status', 'sa_history_sync_state', ['status', 'attempts'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_history_status', table_name='sa_history_sync_state')
    op.drop_table('sa_history_sync_state')
