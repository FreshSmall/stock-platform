"""Pipeline topology + run/step bookkeeping (V2.5 BP-V2.5-001/002).

Single source of truth for the daily/weekly job topology. Four consumers:

* ``app.scheduler`` — cron registration iterates :data:`PIPELINE_TOPOLOGY`;
* ``app.services.admin_service`` — ``run_task`` / ``_finalize_run`` call
  :func:`on_step_start` / :func:`on_step_finish` so EVERY execution path
  (scheduler, retry, manual admin trigger) lands in the same step row;
* ``app.services.quality_service`` — the 08:00 patrol calls
  :func:`patrol_missing` for missing/failed steps;
* the admin pipeline view — :func:`daily_report` / :func:`summary`.

All bookkeeping entry points swallow their own exceptions: a broken metric
row must never take a data task down with it (set ``pipeline_enabled=False``
to disable bookkeeping entirely without rolling back the migration).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import desc, func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.pipeline import (
    RUN_FAILED,
    RUN_PARTIAL,
    RUN_RUNNING,
    RUN_SUCCESS,
    SaPipelineRun,
    SaPipelineStep,
    STEP_FAILED,
    STEP_SKIPPED,
    STEP_SUCCESS,
)
from app.models.quality import SaDataQualityCheck, SaDataQualityRule

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Topology (single source of truth)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StepSpec:
    """One pipeline step: scheduler job, admin task and pipeline node in one.

    ``step_key`` doubles as the scheduler job id and the ``sa_admin_task_log``
    task name. ``critical`` marks data-producing steps — their failure flips
    the whole run to ``failed`` instead of ``partial``. ``retry_max=None``
    falls back to ``settings.pipeline_step_retry_max``.
    """

    step_key: str
    pipeline: str  # "daily" | "weekly"
    hour: int
    minute: int
    weekday: str = "mon-fri"
    critical: bool = False
    retry_max: int | None = None

    @property
    def cron(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d} {self.weekday}"


PIPELINE_TOPOLOGY: list[StepSpec] = [
    StepSpec("pool_sync", "daily", 16, 25),
    StepSpec("index_sync", "daily", 16, 35),
    StepSpec("sentiment_sync", "daily", 16, 45),
    StepSpec("north_flow_sync", "daily", 17, 0),
    StepSpec("money_flow_detail_sync", "daily", 17, 5),
    StepSpec("sector_sync", "daily", 17, 10),
    StepSpec("daily_k_sync", "daily", 17, 30, critical=True),
    StepSpec("dragon_tiger_sync", "daily", 18, 0),
    StepSpec("market_agent_sync", "daily", 18, 10),
    StepSpec("review_agent_sync", "daily", 18, 20),
    StepSpec("trade_status_sync", "daily", 19, 0, critical=True),
    StepSpec("finance_sync", "daily", 19, 30),
    StepSpec("paper_tick", "daily", 20, 0, critical=True),
    StepSpec("delist_sync", "weekly", 9, 0, weekday="sat"),
    StepSpec("factor_health_check", "weekly", 9, 30, weekday="sat"),
    StepSpec("industry_map_sync", "weekly", 9, 0, weekday="sun"),
]

# Admin task names that book into a DIFFERENT topology step:
# - daily_k_sync_retry (the 23:00 compensation) merges back into daily_k_sync
#   (attempts/status update the same step row, no new step_key);
# - finance_sync_nightly (uncapped nightly run, 3600s deadline) books into the
#   finance_sync step so manual capped runs and nightly runs share one node.
TASK_ALIASES: dict[str, str] = {
    "daily_k_sync_retry": "daily_k_sync",
    "finance_sync_nightly": "finance_sync",
}

# Cron ids the scheduler registers that are NOT topology steps (the 08:00
# patrol; alias job ids are topology-adjacent and declared in TASK_ALIASES).
NON_TOPOLOGY_CRON_JOBS: set[str] = {"quality_check"}

_STEP_INDEX: dict[str, StepSpec] = {s.step_key: s for s in PIPELINE_TOPOLOGY}
# seq is the topology position (frontend timeline order).
_STEP_SEQ: dict[str, int] = {s.step_key: i + 1 for i, s in enumerate(PIPELINE_TOPOLOGY)}

CHECK_NAME = "pipeline_health"
_RETRY_PREFIX = "scheduler:retry"

# Threshold rules seeded on first use (fail >= 1 offending step).
DEFAULT_RULES: dict[tuple[str, str], tuple[float | None, float]] = {
    (CHECK_NAME, "step_failed"): (None, 1.0),
    (CHECK_NAME, "step_missing"): (None, 1.0),
}


def step_spec(task_name: str) -> StepSpec | None:
    """Topology spec for ``task_name`` (None → not a pipeline step).

    Alias task names (``daily_k_sync_retry`` / ``finance_sync_nightly``)
    resolve to their target step so every variant books into one node.
    """
    return _STEP_INDEX.get(TASK_ALIASES.get(task_name, task_name))


def effective_retry_max(spec: StepSpec) -> int:
    return settings.pipeline_step_retry_max if spec.retry_max is None else spec.retry_max


# ---------------------------------------------------------------------------
# Pure state machine (unit-test main battlefield)
# ---------------------------------------------------------------------------


def recompute_run_status(step_states: list[tuple[str, bool]], final: bool) -> str:
    """Run status from its steps.

    ``step_states`` is one ``(step_status, critical)`` per MATERIALIZED step;
    steps that never fired have no row (the 08:00 patrol flags those). ``final``
    means the pipeline's last topology step has reached a terminal state —
    only then can the run leave ``running``.
    """
    if any(s == STEP_FAILED and crit for s, crit in step_states):
        return RUN_FAILED
    if not final:
        return RUN_RUNNING
    if any(s == STEP_FAILED for s, _ in step_states):
        return RUN_PARTIAL
    if step_states and all(
        s in (STEP_SUCCESS, STEP_SKIPPED) for s, _ in step_states
    ):
        return RUN_SUCCESS
    return RUN_RUNNING


def _run_is_final(pipeline: str, steps: list[SaPipelineStep]) -> bool:
    """Whether the pipeline's last topology step reached a terminal state."""
    last_key = [s.step_key for s in PIPELINE_TOPOLOGY if s.pipeline == pipeline][-1]
    for st in steps:
        if st.step_key == last_key:
            return st.status in (STEP_SUCCESS, STEP_FAILED, STEP_SKIPPED)
    return False


# ---------------------------------------------------------------------------
# Bookkeeping hooks (called from admin_service; must never raise)
# ---------------------------------------------------------------------------


def _bookkeeping_enabled(task_name: str) -> bool:
    if not settings.pipeline_enabled:
        return False
    return task_name in _STEP_INDEX


def _step_run_date(spec: StepSpec) -> date | None:
    """Natural run date for a step firing now; None = no bookkeeping.

    Daily steps only keep bookkeeping on trading days (weekend/holiday runs
    would pollute the trade-day timeline; the tasks themselves still run with
    their own internal guards). Weekly steps always bookkeep — their cron
    already restricts them to the weekend.
    """
    today = date.today()
    if spec.pipeline == "daily":
        from app.scheduler import _is_trade_day  # lazy: avoid import cycle

        if not _is_trade_day(today):
            return None
    return today


def on_step_start(task_name: str, task_log_id: int, triggered_by: str) -> None:
    """Create/refresh the run + step row when a topology task starts."""
    try:
        spec = step_spec(task_name)
        if spec is None or not settings.pipeline_enabled:
            return
        run_date = _step_run_date(spec)
        if run_date is None:
            return
        db = _session()
        try:
            run = _ensure_run(db, run_date, spec.pipeline)
            now = datetime.now()
            stmt = mysql_insert(SaPipelineStep).values(
                run_id=run.id,
                step_key=spec.step_key,
                seq=_STEP_SEQ[spec.step_key],
                status="running",
                attempts=1,
                started_at=now,
                task_log_id=task_log_id,
            )
            stmt = stmt.on_duplicate_key_update(
                status="running",
                attempts=SaPipelineStep.attempts + 1,
                started_at=now,
                task_log_id=task_log_id,
                error=None,
            )
            db.execute(stmt)
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - bookkeeping must never kill the task
        logger.exception("pipeline on_step_start(%s) failed", task_name)


def on_step_finish(
    task_name: str,
    task_log_id: int,
    status: str,
    error: str | None,
    triggered_by: str,
) -> None:
    """Finalize the step row, recompute the run, alert/retry as needed."""
    try:
        spec = step_spec(task_name)
        if spec is None or not settings.pipeline_enabled:
            return
        run_date = _step_run_date(spec)
        if run_date is None:
            return
        db = _session()
        try:
            run = _ensure_run(db, run_date, spec.pipeline)
            step = db.execute(
                select(SaPipelineStep).where(
                    SaPipelineStep.run_id == run.id,
                    SaPipelineStep.step_key == spec.step_key,
                )
            ).scalar_one_or_none()
            if step is None:
                # finish without start (bookkeeping enabled mid-run): backfill
                step = SaPipelineStep(
                    run_id=run.id,
                    step_key=spec.step_key,
                    seq=_STEP_SEQ[spec.step_key],
                    attempts=1,
                )
                db.add(step)
                db.flush()
            now = datetime.now()
            started = step.started_at or now
            step.status = STEP_SUCCESS if status == "success" else STEP_FAILED
            step.finished_at = now
            step.duration_ms = int((now - started).total_seconds() * 1000)
            step.error = (error or None) and error[:8192]
            step.task_log_id = task_log_id
            db.flush()

            steps = (
                db.execute(
                    select(SaPipelineStep).where(SaPipelineStep.run_id == run.id)
                )
                .scalars()
                .all()
            )
            final = _run_is_final(spec.pipeline, steps)
            states = [
                (st.status, _STEP_INDEX[st.step_key].critical)
                for st in steps
                if st.step_key in _STEP_INDEX
            ]
            new_status = recompute_run_status(states, final)
            run.status = new_status
            if final:
                run.finished_at = now
            db.commit()

            if step.status == STEP_FAILED:
                retrying = (
                    triggered_by == "scheduler"
                    and step.attempts <= effective_retry_max(spec)
                )
                if retrying:
                    _schedule_retry(spec, step.attempts)
                else:
                    _write_alert(
                        db,
                        run_date,
                        f"step_failed:{spec.step_key}",
                        {
                            "step": spec.step_key,
                            "attempts": step.attempts,
                            "triggered_by": triggered_by,
                            "error": (error or "")[:2000],
                        },
                    )
                    db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        logger.exception("pipeline on_step_finish(%s) failed", task_name)


def _ensure_run(db: Session, run_date: date, pipeline: str) -> SaPipelineRun:
    stmt = mysql_insert(SaPipelineRun).values(
        run_date=run_date,
        pipeline_type=pipeline,
        status=RUN_RUNNING,
        started_at=datetime.now(),
    )
    stmt = stmt.on_duplicate_key_update(status=RUN_RUNNING)
    db.execute(stmt)
    return db.execute(
        select(SaPipelineRun).where(
            SaPipelineRun.run_date == run_date,
            SaPipelineRun.pipeline_type == pipeline,
        )
    ).scalar_one()


def _schedule_retry(spec: StepSpec, attempts: int) -> None:
    """Register a one-shot in-process retry job (lost on restart by design)."""
    try:
        from apscheduler.triggers.date import DateTrigger

        from app.scheduler import get_scheduler

        sched = get_scheduler()
        if sched is None:
            return
        gap = settings.pipeline_step_retry_gap_min
        sched.add_job(
            retry_step,
            DateTrigger(run_date=datetime.now() + timedelta(minutes=gap)),
            args=[spec.step_key],
            id=f"retry:{spec.step_key}:{attempts}",
            replace_existing=True,
        )
        logger.warning(
            "pipeline step %s failed (attempt %d) — retry scheduled in %d min",
            spec.step_key,
            attempts,
            gap,
        )
    except Exception:  # noqa: BLE001
        logger.exception("scheduling retry for %s failed", spec.step_key)


def retry_step(task_name: str) -> None:
    """Retry job target — re-runs the task through the normal admin path."""
    from app.services import admin_service

    try:
        admin_service.run_task(task_name, triggered_by=_RETRY_PREFIX)
    except Exception:  # noqa: BLE001 - same contract as _run_admin_task
        logger.exception("pipeline retry of %s failed", task_name)


# ---------------------------------------------------------------------------
# Alerts (quality-table family, paper_health precedent)
# ---------------------------------------------------------------------------


def _write_alert(db: Session, check_date: date, metric: str, detail: dict) -> None:
    stmt = mysql_insert(SaDataQualityCheck).values(
        check_date=check_date,
        check_name=CHECK_NAME,
        metric_name=metric,
        metric_value=1,
        status="fail",
        detail=json.dumps(detail, ensure_ascii=False, default=str)[:60000],
    )
    stmt = stmt.on_duplicate_key_update(
        metric_value=stmt.inserted.metric_value,
        status=stmt.inserted.status,
        detail=stmt.inserted.detail,
    )
    db.execute(stmt)


def ensure_rules(db: Session) -> None:
    """Seed the two pipeline_health threshold rules on first use."""
    existing = {
        (c, m)
        for c, m in db.execute(
            select(SaDataQualityRule.check_name, SaDataQualityRule.metric_name).where(
                SaDataQualityRule.check_name == CHECK_NAME
            )
        ).all()
    }
    for (check, metric), (warn, fail) in DEFAULT_RULES.items():
        if (check, metric) in existing:
            continue
        db.add(
            SaDataQualityRule(
                check_name=check,
                metric_name=metric,
                warn_threshold=None if warn is None else warn,
                fail_threshold=fail,
                enabled=1,
            )
        )
    db.commit()


# ---------------------------------------------------------------------------
# 08:00 patrol integration (quality_service calls this)
# ---------------------------------------------------------------------------


def patrol_missing(db: Session, d: date) -> tuple[float | None, dict]:
    """Missing/failed topology steps for the patrol's settled date ``d``.

    Checks the daily run of ``d`` plus the most recent weekend runs at/before
    ``d`` (weekly steps). Bounded by the pipeline epoch (first run row ever):
    dates before the feature went live are not expected to have runs. Also
    idempotently refreshes ``step_failed:<key>`` alert rows for still-failed
    steps so a crash between failure and alert still surfaces.

    :return: ``(missing_count, detail)`` — ``None`` when bookkeeping isn't
      live yet (no runs at all), which the patrol renders as a pass-skip.
    """
    epoch = db.execute(select(func.min(SaPipelineRun.run_date))).scalar()
    if epoch is None:
        return None, {"note": "pipeline bookkeeping not live yet"}

    expected: list[tuple[date, str, list[StepSpec]]] = []
    if d >= epoch:
        expected.append(
            (d, "daily", [s for s in PIPELINE_TOPOLOGY if s.pipeline == "daily"])
        )
    # walk back to the Saturday at/before d; its Sunday pair is the weekend run
    sat = d
    while sat.weekday() != 5:  # 5 = Saturday
        sat -= timedelta(days=1)
    sun = sat + timedelta(days=1)
    for wd, pipeline_steps in (
        (sat, [s for s in PIPELINE_TOPOLOGY if s.pipeline == "weekly" and s.weekday == "sat"]),
        (sun, [s for s in PIPELINE_TOPOLOGY if s.pipeline == "weekly" and s.weekday == "sun"]),
    ):
        if wd >= epoch and wd <= d:
            expected.append((wd, "weekly", pipeline_steps))

    missing: list[dict] = []
    for run_date, pipeline, specs in expected:
        steps = (
            db.execute(
                select(SaPipelineStep.step_key, SaPipelineStep.status)
                .join(SaPipelineRun, SaPipelineStep.run_id == SaPipelineRun.id)
                .where(
                    SaPipelineRun.run_date == run_date,
                    SaPipelineRun.pipeline_type == pipeline,
                )
            )
            .all()
        )
        by_key = {k: s for k, s in steps}
        for spec in specs:
            st = by_key.get(spec.step_key)
            if st is None or st not in (STEP_SUCCESS, STEP_SKIPPED):
                missing.append(
                    {"date": str(run_date), "step": spec.step_key, "status": st}
                )
                if st == STEP_FAILED:
                    # refresh the instant alert in case it was never written
                    _write_alert(
                        db,
                        run_date,
                        f"step_failed:{spec.step_key}",
                        {"step": spec.step_key, "source": "patrol_refresh"},
                    )
    db.commit()
    return float(len(missing)), {
        "missing": missing,
        "checked_dates": [str(rd) for rd, _, _ in expected],
    }


# ---------------------------------------------------------------------------
# Query side (admin pipeline view)
# ---------------------------------------------------------------------------


def _session() -> Session:
    from app.core.database import SessionLocal

    return SessionLocal()


def _step_titles() -> dict[str, str]:
    from app.services.admin_service import TASK_TITLES

    return TASK_TITLES


def daily_report(db: Session, run_date: date | None = None) -> dict:
    """One run (latest or by date) with its steps + the day's long tasks."""
    q = select(SaPipelineRun).order_by(desc(SaPipelineRun.run_date))
    if run_date is not None:
        q = q.where(SaPipelineRun.run_date == run_date)
    run = db.execute(q.limit(1)).scalar_one_or_none()
    if run is None:
        return {"run": None, "steps": [], "long_tasks": []}

    steps = (
        db.execute(
            select(SaPipelineStep)
            .where(SaPipelineStep.run_id == run.id)
            .order_by(SaPipelineStep.seq)
        )
        .scalars()
        .all()
    )
    log_ids = [st.task_log_id for st in steps if st.task_log_id]
    logs = {}
    if log_ids:
        from app.models.market_data import SaAdminTaskLog

        rows = db.execute(
            select(SaAdminTaskLog).where(SaAdminTaskLog.id.in_(log_ids))
        ).scalars().all()
        logs = {r.id: r for r in rows}

    titles = _step_titles()
    out_steps = []
    for st in steps:
        log = logs.get(st.task_log_id)
        out_steps.append(
            {
                "step_key": st.step_key,
                "title": titles.get(st.step_key, st.step_key),
                "seq": st.seq,
                "status": st.status,
                "attempts": st.attempts,
                "started_at": st.started_at,
                "finished_at": st.finished_at,
                "duration_ms": st.duration_ms,
                "error": st.error,
                "task_log": None
                if log is None
                else {
                    "id": log.id,
                    "status": log.status,
                    "rows_affected": log.rows_affected,
                    "progress_done": log.progress_done,
                    "progress_total": log.progress_total,
                    "result": log.result_json,
                    "error": log.error,
                    "triggered_by": log.triggered_by,
                },
            }
        )

    from app.models.market_data import SaAdminTaskLog

    day_start = datetime.combine(run.run_date, datetime.min.time())
    topo_keys = set(_STEP_INDEX.keys()) | set(TASK_ALIASES.keys())
    long_tasks = (
        db.execute(
            select(SaAdminTaskLog)
            .where(
                SaAdminTaskLog.started_at >= day_start,
                SaAdminTaskLog.started_at < day_start + timedelta(days=1),
                SaAdminTaskLog.task_name.not_in(list(topo_keys)),
            )
            .order_by(desc(SaAdminTaskLog.started_at))
            .limit(30)
        )
        .scalars()
        .all()
    )
    return {
        "run": {
            "run_date": str(run.run_date),
            "pipeline_type": run.pipeline_type,
            "status": run.status,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
        },
        "steps": out_steps,
        "long_tasks": [
            {
                "task_name": t.task_name,
                "title": titles.get(t.task_name, t.task_name),
                "status": t.status,
                "started_at": t.started_at,
                "finished_at": t.finished_at,
                "rows_affected": t.rows_affected,
                "error": t.error,
            }
            for t in long_tasks
        ],
    }


def summary(db: Session, days: int = 30) -> list[dict]:
    """Per-run aggregates for the history heat strip."""
    runs = (
        db.execute(
            select(SaPipelineRun)
            .order_by(desc(SaPipelineRun.run_date))
            .limit(days)
        )
        .scalars()
        .all()
    )
    if not runs:
        return []
    counts: dict[int, dict[str, int]] = {}
    rows = db.execute(
        select(
            SaPipelineStep.run_id,
            SaPipelineStep.status,
            func.count(SaPipelineStep.id),
        )
        .where(SaPipelineStep.run_id.in_([r.id for r in runs]))
        .group_by(SaPipelineStep.run_id, SaPipelineStep.status)
    ).all()
    for run_id, status, n in rows:
        counts.setdefault(run_id, {})[status] = n
    return [
        {
            "run_date": str(r.run_date),
            "pipeline_type": r.pipeline_type,
            "status": r.status,
            "total": sum(counts.get(r.id, {}).values()),
            "ok": counts.get(r.id, {}).get(STEP_SUCCESS, 0),
            "failed": counts.get(r.id, {}).get(STEP_FAILED, 0),
        }
        for r in runs
    ]
