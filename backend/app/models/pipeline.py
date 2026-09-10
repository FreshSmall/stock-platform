"""ORM mappings for the V2.5 pipeline-run/step tables (BP-V2.5-001).

* :class:`SaPipelineRun`  - one pipeline instance per (date, pipeline_type).
* :class:`SaPipelineStep` - one row per topology step inside a run.

The topology itself (which steps exist, in what order, at what time) lives in
``app.services.pipeline_service.PIPELINE_TOPOPOLOGY``; these tables only
materialize what actually happened, driven by hooks inside
``admin_service.run_task`` / ``_finalize_run``. ``task_log_id`` is a logical
reference to ``sa_admin_task_log.id`` (no FK, house style) so a step drill-down
can join the task-level record without coupling migrations.
"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# Step status values (run status shares the vocabulary plus "partial").
STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_SUCCESS = "success"
STEP_FAILED = "failed"
STEP_SKIPPED = "skipped"

RUN_RUNNING = "running"
RUN_SUCCESS = "success"
RUN_PARTIAL = "partial"
RUN_FAILED = "failed"
RUN_SKIPPED = "skipped"


class SaPipelineRun(Base):
    """One pipeline instance for one calendar date.

    ``pipeline_type`` is ``daily`` (the weekday data pipeline) or ``weekly``
    (the weekend maintenance jobs). Non-trading days create no run at all —
    the bookkeeping hook skips them; the underlying tasks still execute with
    their own internal guards (scheduler behaviour is unchanged).
    """

    __tablename__ = "sa_pipeline_run"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    pipeline_type: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default=RUN_RUNNING)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    __table_args__ = (
        UniqueConstraint("run_date", "pipeline_type", name="uk_date_type"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaPipelineRun(id={self.id!r}, run_date={self.run_date!r}, "
            f"pipeline_type={self.pipeline_type!r}, status={self.status!r})"
        )


class SaPipelineStep(Base):
    """One topology step's materialized outcome inside a run.

    ``attempts`` starts at 1 on the first execution and increments on every
    retry or manual re-run (upsert by UK). ``task_log_id`` links the step to
    the ``sa_admin_task_log`` row of its latest execution so the admin
    pipeline view can drill down without re-running anything.
    """

    __tablename__ = "sa_pipeline_step"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    step_key: Mapped[str] = mapped_column(String(50), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default=STEP_PENDING)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    error: Mapped[Optional[str]] = mapped_column(Text)
    task_log_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    __table_args__ = (
        UniqueConstraint("run_id", "step_key", name="uk_run_step"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SaPipelineStep(id={self.id!r}, run_id={self.run_id!r}, "
            f"step_key={self.step_key!r}, status={self.status!r}, "
            f"attempts={self.attempts!r})"
        )
