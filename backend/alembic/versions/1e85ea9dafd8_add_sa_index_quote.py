"""add sa_index_quote

Creates the ``sa_index_quote`` table for daily market-index quotes
(上证指数/深证成指/创业板指). ``index_code`` carries the exchange prefix
(``sh000001``) to avoid colliding with stock codes in ``daily_prices``
(where ``000001`` is 平安银行, not 上证指数).

Hand-edited from autogenerate output: removed all spurious drop/alter noise
against the read-only external tables, keeping only the single CREATE.

Revision ID: 1e85ea9dafd8
Revises: a34015da0db5
Create Date: 2026-07-30 22:55:46.810353
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '1e85ea9dafd8'
down_revision: Union[str, Sequence[str], None] = 'a34015da0db5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sa_index_quote',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('index_code', sa.String(length=20), nullable=False),
        sa.Column('index_name', sa.String(length=50), nullable=True),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('open', sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column('close', sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column('high', sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column('low', sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column('pct_change', sa.Numeric(precision=8, scale=4), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('index_code', 'trade_date', name='uk_index_date'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )


def downgrade() -> None:
    op.drop_table('sa_index_quote')
