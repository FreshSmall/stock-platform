"""init sa tables

Creates the 8 application-managed ``sa_``-prefixed tables in the
``stock_analysis`` database:

* ``sa_user``
* ``sa_ai_analysis``
* ``sa_ai_chat_session``
* ``sa_ai_chat_message``
* ``sa_backtest_run``
* ``sa_backtest_result``
* ``sa_money_flow``
* ``sa_financial_extra``

This migration was produced by ``alembic revision --autogenerate`` and then
HAND-EDITED. Autogenerate also wanted to drop or alter every existing
read-only table populated by external pipelines (``daily_prices``,
``stock_pool``, ``stocks``, ``stock_signal``, ``recommend_result``, ...).
Those edits have been removed: this migration ONLY touches the ``sa_``
tables, leaving all pre-existing tables untouched.

Revision ID: c1b2de099fa7
Revises:
Create Date: 2026-07-29 00:33:40.476408

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = 'c1b2de099fa7'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # All ``sa_`` tables are created with InnoDB + utf8mb4 to match the DDL.
    op.create_table(
        'sa_user',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('username', sa.String(length=50), nullable=False),
        sa.Column('password_hash', sa.String(length=128), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('username', name='uk_username'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_table(
        'sa_ai_analysis',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('request_id', sa.String(length=64), nullable=False),
        sa.Column('stock_code', sa.String(length=10), nullable=False),
        sa.Column('score', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('score_fundamental', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('score_technical', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('score_capital', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('score_news', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('score_risk', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('fundamentals', sa.Text(), nullable=True),
        sa.Column('technicals', sa.Text(), nullable=True),
        sa.Column('capital', sa.Text(), nullable=True),
        sa.Column('news', sa.Text(), nullable=True),
        sa.Column('risk', sa.Text(), nullable=True),
        sa.Column('full_text', mysql.MEDIUMTEXT(), nullable=True),
        sa.Column('user_id', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('request_id', name='uk_request_id'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_index('idx_code_created', 'sa_ai_analysis', ['stock_code', 'created_at'], unique=False)
    op.create_index('idx_user', 'sa_ai_analysis', ['user_id'], unique=False)
    op.create_table(
        'sa_ai_chat_session',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('title', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id', name='uk_session_id'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_index('idx_user', 'sa_ai_chat_session', ['user_id'], unique=False)
    op.create_table(
        'sa_ai_chat_message',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('role', sa.String(length=10), nullable=False),
        sa.Column('content', mysql.MEDIUMTEXT(), nullable=False),
        sa.Column('tool_calls', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_index('idx_session_created', 'sa_ai_chat_message', ['session_id', 'created_at'], unique=False)
    op.create_table(
        'sa_backtest_run',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('run_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=True),
        sa.Column('strategy', sa.String(length=20), nullable=False),
        sa.Column('params', sa.JSON(), nullable=False),
        sa.Column('stock_pool', sa.JSON(), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=False),
        sa.Column('initial_cash', sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column('commission', sa.Numeric(precision=6, scale=4), server_default=sa.text("'0.0003'"), nullable=False),
        sa.Column('slippage', sa.Numeric(precision=6, scale=4), server_default=sa.text("'0.0001'"), nullable=False),
        sa.Column('status', sa.String(length=20), server_default=sa.text("'pending'"), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', name='uk_run_id'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_index('idx_user', 'sa_backtest_run', ['user_id'], unique=False)
    op.create_table(
        'sa_backtest_result',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('run_id', sa.String(length=64), nullable=False),
        sa.Column('return_rate', sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column('max_drawdown', sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column('sharpe', sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column('win_rate', sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column('equity_curve', sa.JSON(), nullable=True),
        sa.Column('trades', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', name='uk_run_id'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_table(
        'sa_money_flow',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('stock_code', sa.String(length=10), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('main_net_inflow', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stock_code', 'trade_date', name='uk_code_date'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_index('idx_date', 'sa_money_flow', ['trade_date'], unique=False)
    op.create_table(
        'sa_financial_extra',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('stock_code', sa.String(length=10), nullable=False),
        sa.Column('report_date', sa.Date(), nullable=False),
        sa.Column('roe', sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column('eps', sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column('revenue_growth', sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column('profit_growth', sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stock_code', 'report_date', name='uk_code_report'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )


def downgrade() -> None:
    # Mirror of upgrade(): drop ONLY the ``sa_`` tables, in reverse
    # dependency order. Existing read-only tables are never touched.
    op.drop_index('idx_date', table_name='sa_money_flow')
    op.drop_table('sa_money_flow')
    op.drop_table('sa_financial_extra')
    op.drop_index('idx_user', table_name='sa_backtest_run')
    op.drop_table('sa_backtest_run')
    op.drop_table('sa_backtest_result')
    op.drop_index('idx_user', table_name='sa_ai_chat_session')
    op.drop_table('sa_ai_chat_session')
    op.drop_index('idx_session_created', table_name='sa_ai_chat_message')
    op.drop_table('sa_ai_chat_message')
    op.drop_index('idx_user', table_name='sa_ai_analysis')
    op.drop_index('idx_code_created', table_name='sa_ai_analysis')
    op.drop_table('sa_ai_analysis')
    op.drop_table('sa_user')
