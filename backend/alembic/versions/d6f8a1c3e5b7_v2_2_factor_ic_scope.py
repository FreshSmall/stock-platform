"""V2.2: sa_factor_ic pool/neutralized scope dimensions

Revision ID: d6f8a1c3e5b7
Revises: a9c4e2f7b1d3
Create Date: 2026-09-05

BP-V2.2-002: IC series are persisted per (factor, date, horizon) but must
coexist under different sample scopes — universe pool (current / pit) and
neutralization mode (none / industry / industry_mcap). Two NOT NULL columns
with server defaults keep the (empty) table consistent, and the unique key
is rebuilt over the five-column scope. Table has never been written, so the
key change carries no data risk.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d6f8a1c3e5b7"
down_revision: Union[str, Sequence[str], None] = "a9c4e2f7b1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sa_factor_ic",
        sa.Column(
            "pool", sa.String(length=8), nullable=False, server_default="current"
        ),
    )
    op.add_column(
        "sa_factor_ic",
        sa.Column(
            "neutralized",
            sa.String(length=16),
            nullable=False,
            server_default="none",
        ),
    )
    op.drop_constraint(
        "uk_factor_date_horizon", "sa_factor_ic", type_="unique"
    )
    op.create_unique_constraint(
        "uk_factor_date_horizon_scope",
        "sa_factor_ic",
        ["factor_code", "trade_date", "horizon", "pool", "neutralized"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uk_factor_date_horizon_scope", "sa_factor_ic", type_="unique"
    )
    op.create_unique_constraint(
        "uk_factor_date_horizon",
        "sa_factor_ic",
        ["factor_code", "trade_date", "horizon"],
    )
    op.drop_column("sa_factor_ic", "neutralized")
    op.drop_column("sa_factor_ic", "pool")
