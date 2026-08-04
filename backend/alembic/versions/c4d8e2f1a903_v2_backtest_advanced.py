"""v2 backtest advanced metrics

Adds 7 columns to ``sa_backtest_result`` for V2 advanced backtest metrics:
calmar / information_ratio / profit_loss_ratio (scalars) and
drawdown_curve / position_curve / benchmark_curve / benchmark_return (curves).

Hand-written (no autogenerate noise against read-only tables).

Revision ID: c4d8e2f1a903
Revises: b7c2f9a1d3e5
Create Date: 2026-08-03 ...
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = 'c4d8e2f1a903'
down_revision: Union[str, Sequence[str], None] = 'b7c2f9a1d3e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('sa_backtest_result',
                  sa.Column('calmar', sa.Numeric(precision=10, scale=4), nullable=True))
    op.add_column('sa_backtest_result',
                  sa.Column('information_ratio', sa.Numeric(precision=10, scale=4), nullable=True))
    op.add_column('sa_backtest_result',
                  sa.Column('profit_loss_ratio', sa.Numeric(precision=10, scale=4), nullable=True))
    op.add_column('sa_backtest_result',
                  sa.Column('drawdown_curve', mysql.JSON(), nullable=True))
    op.add_column('sa_backtest_result',
                  sa.Column('position_curve', mysql.JSON(), nullable=True))
    op.add_column('sa_backtest_result',
                  sa.Column('benchmark_curve', mysql.JSON(), nullable=True))
    op.add_column('sa_backtest_result',
                  sa.Column('benchmark_return', sa.Numeric(precision=10, scale=4), nullable=True))


def downgrade() -> None:
    op.drop_column('sa_backtest_result', 'benchmark_return')
    op.drop_column('sa_backtest_result', 'benchmark_curve')
    op.drop_column('sa_backtest_result', 'position_curve')
    op.drop_column('sa_backtest_result', 'drawdown_curve')
    op.drop_column('sa_backtest_result', 'profit_loss_ratio')
    op.drop_column('sa_backtest_result', 'information_ratio')
    op.drop_column('sa_backtest_result', 'calmar')
