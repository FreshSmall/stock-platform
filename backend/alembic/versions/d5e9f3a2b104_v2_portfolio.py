"""v2 portfolio tables

Creates ``sa_portfolio`` + ``sa_portfolio_holding`` (BP-V2-005).

Revision ID: d5e9f3a2b104
Revises: c4d8e2f1a903
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd5e9f3a2b104'
down_revision: Union[str, Sequence[str], None] = 'c4d8e2f1a903'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sa_portfolio',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=True),
        sa.Column('name', sa.String(length=50), nullable=False),
        sa.Column('description', sa.String(length=200), nullable=True),
        sa.Column('benchmark', sa.String(length=20), nullable=False, server_default='sh000001'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )
    op.create_index('idx_user', 'sa_portfolio', ['user_id'], unique=False)

    op.create_table(
        'sa_portfolio_holding',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('portfolio_id', sa.BigInteger(), nullable=False),
        sa.Column('stock_code', sa.String(length=10), nullable=False),
        sa.Column('weight', sa.Numeric(precision=6, scale=4), nullable=False, server_default='0.5'),
        sa.Column('added_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('portfolio_id', 'stock_code', name='uk_pf_stock'),
        mysql_engine='InnoDB',
        mysql_default_charset='utf8mb4',
    )


def downgrade() -> None:
    op.drop_table('sa_portfolio_holding')
    op.drop_index('idx_user', table_name='sa_portfolio')
    op.drop_table('sa_portfolio')
