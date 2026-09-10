"""V3a: paper trading tables

Revision ID: c8d1e4f6a9b3
Revises: d6f8a1c3e5b7
Create Date: 2026-09-06

BP-V3a-001: five tables for the simulated (paper) portfolio track —
``sa_paper_account`` (strategy config + cash ledger), ``sa_paper_order``
(daily rebalance intents, executed at next open), ``sa_paper_trade`` (fills
with per-item fee breakdown), ``sa_paper_position`` (EOD holdings snapshot)
and ``sa_paper_nav`` (EOD NAV / benchmark series). Unique keys enforce one
order per (account, signal date, stock, side), one position row per
(account, date, stock) and one NAV row per (account, date); plain indexes
cover the (account_id, exec_date) and (account_id, trade_date) lookups the
daily engine issues. FKs follow the existing house style (no ondelete).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c8d1e4f6a9b3"
down_revision: Union[str, Sequence[str], None] = "d6f8a1c3e5b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sa_paper_account",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("initial_cash", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("cash", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="active"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uk_name"),
        mysql_engine="InnoDB",
        mysql_default_charset="utf8mb4",
    )

    op.create_table(
        "sa_paper_order",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("exec_date", sa.Date(), nullable=False),
        sa.Column("stock_code", sa.String(length=12), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column(
            "target_weight", sa.Numeric(precision=10, scale=6), nullable=True
        ),
        sa.Column("shares", sa.Integer(), nullable=True),
        sa.Column(
            "status", sa.String(length=12), nullable=False, server_default="pending"
        ),
        sa.Column("fail_reason", sa.String(length=64), nullable=True),
        sa.Column("filled_trade_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["sa_paper_account.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id",
            "signal_date",
            "stock_code",
            "side",
            name="uk_account_signal_code_side",
        ),
        mysql_engine="InnoDB",
        mysql_default_charset="utf8mb4",
    )
    op.create_index(
        "ix_account_exec",
        "sa_paper_order",
        ["account_id", "exec_date"],
        unique=False,
    )

    op.create_table(
        "sa_paper_trade",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("stock_code", sa.String(length=12), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column("price", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("shares", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column(
            "commission", sa.Numeric(precision=18, scale=6), nullable=False
        ),
        sa.Column(
            "stamp_duty", sa.Numeric(precision=18, scale=6), nullable=False
        ),
        sa.Column(
            "transfer_fee", sa.Numeric(precision=18, scale=6), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["sa_paper_account.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["sa_paper_order.id"]),
        sa.PrimaryKeyConstraint("id"),
        mysql_engine="InnoDB",
        mysql_default_charset="utf8mb4",
    )
    op.create_index(
        "ix_account_trade_date",
        "sa_paper_trade",
        ["account_id", "trade_date"],
        unique=False,
    )

    op.create_table(
        "sa_paper_position",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("stock_code", sa.String(length=12), nullable=False),
        sa.Column("shares", sa.Integer(), nullable=False),
        sa.Column("close_price", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("market_value", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["sa_paper_account.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id", "trade_date", "stock_code", name="uk_account_date_code"
        ),
        mysql_engine="InnoDB",
        mysql_default_charset="utf8mb4",
    )

    op.create_table(
        "sa_paper_nav",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("cash", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("market_value", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("total_asset", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("nav", sa.Numeric(precision=14, scale=6), nullable=False),
        sa.Column("daily_ret", sa.Numeric(precision=12, scale=8), nullable=True),
        sa.Column(
            "benchmark_close", sa.Numeric(precision=14, scale=4), nullable=True
        ),
        sa.Column("benchmark_nav", sa.Numeric(precision=14, scale=6), nullable=True),
        sa.Column("drawdown", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["sa_paper_account.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "trade_date", name="uk_account_date"),
        mysql_engine="InnoDB",
        mysql_default_charset="utf8mb4",
    )


def downgrade() -> None:
    # plain drop_table is enough: MySQL drops the attached FKs/indexes with
    # the table, and dropping an FK-covering index before its table would
    # fail with errno 1553 ("index needed in a foreign key constraint").
    op.drop_table("sa_paper_nav")
    op.drop_table("sa_paper_position")
    op.drop_table("sa_paper_trade")
    op.drop_table("sa_paper_order")
    op.drop_table("sa_paper_account")
