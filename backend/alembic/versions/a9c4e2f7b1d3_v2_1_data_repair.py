"""v2.1 data-repair tables

Creates the V2.1 (spec-004) data-repair tables:

* ``sa_kline_daily``        - raw (un-adjusted) daily K, the target store.
* ``sa_adjust_factor``      - per-day cumulative adjust factor.
* ``sa_stock_lifecycle``    - listing/delisting windows incl. delisted stocks.
* ``sa_daily_trade_status`` - ST/suspension/limit-board tradability flags.
* ``sa_industry_map``       - per-stock industry mapping (stable codes).
* ``sa_kline_sync_state``   - re-ingest tick progress (per stock).
* ``sa_data_quality_rule``  - configurable patrol thresholds (+ seed rows).
* ``sa_data_quality_check`` - one materialized result per (day, check, metric).

Plus ``sa_admin_task_log`` gains long-task progress columns
(progress_done/progress_total/result_json). The legacy ``daily_prices`` /
``stock_pool`` tables are intentionally NOT touched — reads switch via the
``kline_source`` setting after the gray period (see spec-004 实现方案 §3.3).

Revision ID: a9c4e2f7b1d3
Revises: f2b7d9a4c6e8
Create Date: 2026-08-31 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = 'a9c4e2f7b1d3'
down_revision: Union[str, Sequence[str], None] = 'f2b7d9a4c6e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sa_kline_daily',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('stock_code', sa.String(length=10), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('open', sa.Numeric(10, 2), nullable=True),
        sa.Column('close', sa.Numeric(10, 2), nullable=True),
        sa.Column('high', sa.Numeric(10, 2), nullable=True),
        sa.Column('low', sa.Numeric(10, 2), nullable=True),
        sa.Column('volume', sa.BigInteger(), nullable=True, comment='手'),
        sa.Column('amount', sa.Numeric(18, 2), nullable=True, comment='元'),
        sa.Column('pct_change', sa.Numeric(8, 4), nullable=True,
                  comment='源端真实复权涨跌幅(%)，复权锚'),
        sa.Column('turnover', sa.Numeric(8, 4), nullable=True, comment='换手率(%)'),
        sa.Column('source', sa.String(length=10), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stock_code', 'trade_date', name='uk_code_date'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_index('idx_date', 'sa_kline_daily', ['trade_date'], unique=False)

    op.create_table(
        'sa_adjust_factor',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('stock_code', sa.String(length=10), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('adj_factor', sa.Numeric(20, 8), nullable=False),
        sa.Column('anchored', sa.SmallInteger(), nullable=False, server_default='1'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stock_code', 'trade_date', name='uk_code_date'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_index('idx_date', 'sa_adjust_factor', ['trade_date'], unique=False)

    op.create_table(
        'sa_stock_lifecycle',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('stock_code', sa.String(length=10), nullable=False),
        sa.Column('stock_name', sa.String(length=50), nullable=True),
        sa.Column('exchange', sa.String(length=10), nullable=True),
        sa.Column('list_date', sa.Date(), nullable=True),
        sa.Column('delist_date', sa.Date(), nullable=True, comment='NULL=在市'),
        sa.Column('list_status', sa.String(length=10), nullable=False, server_default='L'),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stock_code', name='uk_code'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_index('idx_delist', 'sa_stock_lifecycle', ['delist_date'], unique=False)

    op.create_table(
        'sa_daily_trade_status',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('stock_code', sa.String(length=10), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('is_st', sa.SmallInteger(), nullable=True, comment='NULL=无法回溯'),
        sa.Column('is_suspended', sa.SmallInteger(), nullable=False, server_default='0'),
        sa.Column('limit_status', sa.String(length=20), nullable=False, server_default='none'),
        sa.Column('buy_tradable', sa.SmallInteger(), nullable=False, server_default='1'),
        sa.Column('sell_tradable', sa.SmallInteger(), nullable=False, server_default='1'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stock_code', 'trade_date', name='uk_code_date'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_index('idx_date_status', 'sa_daily_trade_status', ['trade_date', 'limit_status'], unique=False)

    op.create_table(
        'sa_industry_map',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('stock_code', sa.String(length=10), nullable=False),
        sa.Column('industry_code', sa.String(length=20), nullable=False),
        sa.Column('industry_name', sa.String(length=50), nullable=False),
        sa.Column('industry_level', sa.String(length=10), nullable=False, server_default='em'),
        sa.Column('effective_date', sa.Date(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stock_code', 'industry_level', 'effective_date', name='uk_code_level_date'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_index('idx_level_code', 'sa_industry_map', ['industry_level', 'industry_code'], unique=False)

    op.create_table(
        'sa_kline_sync_state',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('stock_code', sa.String(length=10), nullable=False),
        sa.Column('target_start', sa.Date(), nullable=False),
        sa.Column('earliest_bar', sa.Date(), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False, server_default='pending'),
        sa.Column('attempts', sa.SmallInteger(), nullable=False, server_default='0'),
        sa.Column('priority', sa.SmallInteger(), nullable=False, server_default='1'),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stock_code', name='uk_kline_code'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_index('idx_kline_status', 'sa_kline_sync_state', ['status', 'attempts', 'priority'], unique=False)

    op.create_table(
        'sa_data_quality_rule',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('check_name', sa.String(length=50), nullable=False),
        sa.Column('metric_name', sa.String(length=50), nullable=False),
        sa.Column('warn_threshold', sa.Numeric(18, 4), nullable=True),
        sa.Column('fail_threshold', sa.Numeric(18, 4), nullable=False),
        sa.Column('enabled', sa.SmallInteger(), nullable=False, server_default='1'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('check_name', 'metric_name', name='uk_check_metric'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )

    op.create_table(
        'sa_data_quality_check',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('check_date', sa.Date(), nullable=False),
        sa.Column('check_name', sa.String(length=50), nullable=False),
        sa.Column('metric_name', sa.String(length=50), nullable=False),
        sa.Column('metric_value', sa.Numeric(18, 4), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('detail', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('check_date', 'check_name', 'metric_name', name='uk_date_check_metric'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_index('idx_date', 'sa_data_quality_check', ['check_date'], unique=False)

    op.add_column('sa_admin_task_log', sa.Column('progress_done', sa.Integer(), nullable=True))
    op.add_column('sa_admin_task_log', sa.Column('progress_total', sa.Integer(), nullable=True))
    op.add_column('sa_admin_task_log', sa.Column('result_json', sa.Text(), nullable=True))

    # Seed the default patrol thresholds (tunable in-table afterwards).
    rule_tbl = sa.table(
        'sa_data_quality_rule',
        sa.column('check_name', sa.String),
        sa.column('metric_name', sa.String),
        sa.column('warn_threshold', sa.Numeric),
        sa.column('fail_threshold', sa.Numeric),
        sa.column('enabled', sa.SmallInteger),
    )
    op.bulk_insert(rule_tbl, [
        # fail-high counts: any deviation row on the day's fresh bars
        {'check_name': 'adjustment_break', 'metric_name': 'new_row_deviation_count',
         'warn_threshold': 10, 'fail_threshold': 50, 'enabled': 1},
        {'check_name': 'frozen', 'metric_name': 'frozen_stock_count',
         'warn_threshold': 5, 'fail_threshold': 20, 'enabled': 1},
        # fail-low ratio: today's settled rows vs the 15-day baseline max
        {'check_name': 'row_baseline', 'metric_name': 'row_ratio',
         'warn_threshold': 0.95, 'fail_threshold': 0.90, 'enabled': 1},
        # fail-high pct: amount NULL share among the day's new rows
        {'check_name': 'field_missing', 'metric_name': 'amount_missing_pct',
         'warn_threshold': 20, 'fail_threshold': 50, 'enabled': 1},
        # fail-low coverage ratios
        {'check_name': 'coverage', 'metric_name': 'trade_status_coverage',
         'warn_threshold': 0.90, 'fail_threshold': 0.70, 'enabled': 1},
        {'check_name': 'coverage', 'metric_name': 'industry_coverage',
         'warn_threshold': 0.90, 'fail_threshold': 0.70, 'enabled': 1},
        {'check_name': 'coverage', 'metric_name': 'lifecycle_coverage',
         'warn_threshold': 0.99, 'fail_threshold': 0.95, 'enabled': 1},
        # fail-high count: rows with abnormal intraday amplitude (>30%)
        {'check_name': 'amplitude_anomaly', 'metric_name': 'abnormal_rows',
         'warn_threshold': 10, 'fail_threshold': 50, 'enabled': 1},
    ])


def downgrade() -> None:
    op.drop_column('sa_admin_task_log', 'result_json')
    op.drop_column('sa_admin_task_log', 'progress_total')
    op.drop_column('sa_admin_task_log', 'progress_done')
    op.drop_index('idx_date', table_name='sa_data_quality_check')
    op.drop_table('sa_data_quality_check')
    op.drop_table('sa_data_quality_rule')
    op.drop_index('idx_level_code', table_name='sa_industry_map')
    op.drop_table('sa_industry_map')
    op.drop_index('idx_kline_status', table_name='sa_kline_sync_state')
    op.drop_table('sa_kline_sync_state')
    op.drop_index('idx_date_status', table_name='sa_daily_trade_status')
    op.drop_table('sa_daily_trade_status')
    op.drop_index('idx_delist', table_name='sa_stock_lifecycle')
    op.drop_table('sa_stock_lifecycle')
    op.drop_index('idx_date', table_name='sa_adjust_factor')
    op.drop_table('sa_adjust_factor')
    op.drop_index('idx_date', table_name='sa_kline_daily')
    op.drop_table('sa_kline_daily')
