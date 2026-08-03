"""v2 factor tables

Creates the V2 factor tables:

* ``sa_factor_value`` — materialised factor value per (factor, stock, date).
* ``sa_factor_ic``    — factor effectiveness test (IC/IR/win-rate/layered).

Hand-edited from autogenerate output: removed all spurious drop/alter noise
against the read-only external tables, keeping only the two CREATEs.

Revision ID: a311648aed49
Revises: 1e85ea9dafd8
Create Date: 2026-08-03 23:15:05.798735
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = 'a311648aed49'
down_revision: Union[str, Sequence[str], None] = '1e85ea9dafd8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sa_factor_value',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('factor_code', sa.String(length=30), nullable=False),
        sa.Column('stock_code', sa.String(length=10), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('value', sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('factor_code', 'stock_code', 'trade_date', name='uk_factor_code_date'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_index('idx_date_code', 'sa_factor_value', ['trade_date', 'stock_code'], unique=False)

    op.create_table(
        'sa_factor_ic',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('factor_code', sa.String(length=30), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('horizon', sa.Integer(), nullable=False),
        sa.Column('ic', sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column('ir', sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column('win_rate', sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column('layered_returns', mysql.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('factor_code', 'trade_date', 'horizon', name='uk_factor_date_horizon'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )


def downgrade() -> None:
    op.drop_table('sa_factor_ic')
    op.drop_index('idx_date_code', table_name='sa_factor_value')
    op.drop_table('sa_factor_value')
