"""V2.5: pipeline run/step bookkeeping tables

Revision ID: e7a2c9d4f1b6
Revises: c8d1e4f6a9b3
Create Date: 2026-09-10

BP-V2.5-001: ``sa_pipeline_run`` (one pipeline instance per date/type — the
weekday data pipeline and the weekend maintenance pipeline) and
``sa_pipeline_step`` (one row per topology step inside that run, with attempts
/ duration / error and a logical reference to the latest ``sa_admin_task_log``
row). The topology itself lives in code
(``app.services.pipeline_service.PIPELINE_TOPOLOGY``); these tables only
materialize outcomes. Unique keys make every bookkeeping upsert idempotent
(same-day reschedule, manual re-run, retry).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7a2c9d4f1b6"
down_revision: Union[str, Sequence[str], None] = "c8d1e4f6a9b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sa_pipeline_run",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("pipeline_type", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_date", "pipeline_type", name="uk_date_type"),
        mysql_engine="InnoDB",
        mysql_default_charset="utf8mb4",
    )

    op.create_table(
        "sa_pipeline_step",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("step_key", sa.String(length=50), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("task_log_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "step_key", name="uk_run_step"),
        mysql_engine="InnoDB",
        mysql_default_charset="utf8mb4",
    )
    op.create_index("ix_run_id", "sa_pipeline_step", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_run_id", table_name="sa_pipeline_step")
    op.drop_table("sa_pipeline_step")
    op.drop_table("sa_pipeline_run")
