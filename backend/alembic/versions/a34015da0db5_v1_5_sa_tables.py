"""v1.5 sa tables

Creates the 12 application-managed ``sa_``-prefixed tables introduced in V1.5
and adds the ``role``/``status`` columns to ``sa_user``:

* ``sa_minute_price`` / ``sa_dragon_tiger`` / ``sa_dragon_tiger_seat`` /
  ``sa_north_flow`` / ``sa_money_flow_detail`` / ``sa_admin_task_log`` /
  ``sa_stock_industry``
* ``sa_sector`` / ``sa_sector_stock`` / ``sa_sector_daily``
* ``sa_market_sentiment`` / ``sa_limit_up_streak``
* ALTER ``sa_user`` ADD ``role``, ``status``

This migration was produced by ``alembic revision --autogenerate`` and then
HAND-EDITED. Autogenerate also wanted to drop or alter every existing read-only
table populated by external pipelines (``minute_prices``, ``stock_signal``,
``recommend_result``, ``chan_signal``, ``screen_result``, ``stock_signal_log``,
``job_runs``, ``stocks``, ``daily_prices``, ``stock_pool``, ``chip_distribution``)
and to strip column/table comments. Those edits have been removed: this
migration ONLY touches the ``sa_`` tables (create) plus ``sa_user`` (alter),
leaving all pre-existing tables untouched — same convention as the init
migration ``c1b2de099fa7``.

The ``role``/``status`` columns are added with a ``server_default`` so existing
``sa_user`` rows backfill cleanly (role='user', status=1) instead of failing on
NOT NULL without a default.

Revision ID: a34015da0db5
Revises: c1b2de099fa7
Create Date: 2026-07-29 23:36:12.500311

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = 'a34015da0db5'
down_revision: Union[str, Sequence[str], None] = 'c1b2de099fa7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # All ``sa_`` tables are created with InnoDB + utf8mb4 to match the DDL.

    # --- market_data.py ---
    op.create_table(
        'sa_minute_price',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('stock_code', sa.String(length=10), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('trade_time', sa.DateTime(), nullable=False),
        sa.Column('period', sa.SmallInteger(), nullable=False),
        sa.Column('open', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('close', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('high', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('low', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('volume', sa.BigInteger(), nullable=True),
        sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stock_code', 'period', 'trade_time', name='uk_code_period_time'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_table(
        'sa_dragon_tiger',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('stock_code', sa.String(length=10), nullable=False),
        sa.Column('stock_name', sa.String(length=50), nullable=True),
        sa.Column('reason', sa.String(length=100), nullable=True),
        sa.Column('net_buy', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column('buy_amount', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column('sell_amount', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('trade_date', 'stock_code', name='uk_date_code'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_table(
        'sa_dragon_tiger_seat',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('stock_code', sa.String(length=10), nullable=False),
        sa.Column('side', sa.SmallInteger(), nullable=False),
        sa.Column('rank', sa.SmallInteger(), nullable=False),
        sa.Column('seat_name', sa.String(length=100), nullable=False),
        sa.Column('buy_amount', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column('sell_amount', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column('net_amount', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column('is_institution', sa.SmallInteger(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('trade_date', 'stock_code', 'side', 'rank', name='uk_date_code_side_rank'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_table(
        'sa_north_flow',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('channel', sa.String(length=10), nullable=False),
        sa.Column('net_buy', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column('buy_amount', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column('sell_amount', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('trade_date', 'channel', name='uk_date_channel'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_table(
        'sa_money_flow_detail',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('stock_code', sa.String(length=10), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('super_net', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column('big_net', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column('medium_net', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column('small_net', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stock_code', 'trade_date', name='uk_code_date'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_table(
        'sa_admin_task_log',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('task_name', sa.String(length=50), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('rows_affected', sa.Integer(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('triggered_by', sa.String(length=50), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_index(
        'idx_task_started', 'sa_admin_task_log',
        ['task_name', 'started_at'], unique=False,
    )
    op.create_table(
        'sa_stock_industry',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('stock_code', sa.String(length=10), nullable=False),
        sa.Column('industry', sa.String(length=50), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stock_code', name='uk_code'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )

    # --- sector.py ---
    op.create_table(
        'sa_sector',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('sector_code', sa.String(length=20), nullable=False),
        sa.Column('sector_name', sa.String(length=50), nullable=False),
        sa.Column('sector_type', sa.String(length=10), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('sector_code', 'sector_type', name='uk_code_type'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_table(
        'sa_sector_stock',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('sector_code', sa.String(length=20), nullable=False),
        sa.Column('sector_type', sa.String(length=10), nullable=False),
        sa.Column('stock_code', sa.String(length=10), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('sector_code', 'sector_type', 'stock_code', name='uk_sector_stock'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_index(
        'idx_stock', 'sa_sector_stock', ['stock_code'], unique=False,
    )
    op.create_table(
        'sa_sector_daily',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('sector_code', sa.String(length=20), nullable=False),
        sa.Column('sector_type', sa.String(length=10), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('pct_change', sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column('limit_up_count', sa.Integer(), nullable=True),
        sa.Column('main_net_inflow', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column('leader_code', sa.String(length=10), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('sector_code', 'sector_type', 'trade_date', name='uk_sector_date'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_index(
        'idx_date_type', 'sa_sector_daily', ['trade_date', 'sector_type'], unique=False,
    )

    # --- sentiment.py ---
    op.create_table(
        'sa_market_sentiment',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('limit_up_count', sa.Integer(), nullable=True),
        sa.Column('limit_down_count', sa.Integer(), nullable=True),
        sa.Column('failed_limit_count', sa.Integer(), nullable=True),
        sa.Column('seal_rate', sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column('max_streak', sa.Integer(), nullable=True),
        sa.Column('up_count', sa.Integer(), nullable=True),
        sa.Column('down_count', sa.Integer(), nullable=True),
        sa.Column('streak_ladder', mysql.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('trade_date', name='uk_date'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_table(
        'sa_limit_up_streak',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('stock_code', sa.String(length=10), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('streak_days', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stock_code', 'trade_date', name='uk_code_date'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )

    # --- user.py: add role/status (with server_default for existing rows) ---
    op.add_column(
        'sa_user',
        sa.Column('role', sa.String(length=10), nullable=False, server_default='user'),
    )
    op.add_column(
        'sa_user',
        sa.Column('status', sa.SmallInteger(), nullable=False, server_default=sa.text('1')),
    )


def downgrade() -> None:
    op.drop_column('sa_user', 'status')
    op.drop_column('sa_user', 'role')
    op.drop_table('sa_limit_up_streak')
    op.drop_table('sa_market_sentiment')
    op.drop_index('idx_date_type', table_name='sa_sector_daily')
    op.drop_table('sa_sector_daily')
    op.drop_index('idx_stock', table_name='sa_sector_stock')
    op.drop_table('sa_sector_stock')
    op.drop_table('sa_sector')
    op.drop_table('sa_stock_industry')
    op.drop_index('idx_task_started', table_name='sa_admin_task_log')
    op.drop_table('sa_admin_task_log')
    op.drop_table('sa_money_flow_detail')
    op.drop_table('sa_north_flow')
    op.drop_table('sa_dragon_tiger_seat')
    op.drop_table('sa_dragon_tiger')
    op.drop_table('sa_minute_price')
