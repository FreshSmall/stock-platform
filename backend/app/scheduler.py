"""APScheduler job registration.

Jobs:
- ``daily_k_sync``: weekdays 17:30 Asia/Shanghai → incremental daily-K sync.
- ``daily_k_sync_retry``: weekdays 23:00 → if today's row count is far below
  the recent baseline (partial 17:30 run, even across a restart), re-run the
  full sync; otherwise retry only the codes that failed in the 17:30 run.
  Skipped (no-op) when the 17:30 run had zero failures.

The scheduler is NOT started on import (that would create side effects and
background threads during tests / collection). Call :func:`init_scheduler`
from the FastAPI lifespan in :mod:`app.main`.
"""

import logging
from datetime import date, timedelta

from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MISSED,
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select

logger = logging.getLogger(__name__)


def _on_scheduler_event(event) -> None:
    """Log scheduler outcomes explicitly so misfires/errors aren't silent.

    Without this APScheduler prints a generic "was missed by N" line per job
    with no level/context; this raises misfires to WARNING and errors to ERROR
    so they stand out in the log.
    """
    # event.code is one of EVENT_JOB_*; job_id/exception live on the event.
    if event.code == EVENT_JOB_MISSED:
        logger.warning(
            "job %s MISSED its scheduled time (scheduled run was not executed)",
            getattr(event, "job_id", "?"),
        )
    elif event.code == EVENT_JOB_ERROR:
        logger.error(
            "job %s raised: %r",
            getattr(event, "job_id", "?"),
            getattr(event, "exception", None),
        )

_scheduler: BackgroundScheduler | None = None

# Look-back window for the incremental pull.
_LOOKBACK_DAYS = 7

# Codes that failed in the 17:30 main run, replayed by the 23:00 retry job.
# Process-local state — a restart clears it, which is safe: with no failure
# record the retry is a no-op and the next 17:30 run still covers everything.
_last_run_failed_codes: list[str] = []


def _sync_codes(db, codes, start: str, end: str) -> tuple[int, list[str]]:
    """Sync ``codes`` over ``[start, end]`` and return ``(rows, failed)``.

    Shared by the 17:30 main run and the 23:00 retry. A failure on one code
    logs an error but does not abort the run; the failing code is collected
    into the returned ``failed`` list so the caller can replay it.
    """
    from app.data import sync_daily

    total = 0
    failed: list[str] = []
    for code in codes:
        try:
            total += sync_daily.sync_one_stock(db, code, start, end)
        except Exception as e:  # noqa: BLE001 - log and continue per code
            logger.error("sync failed for %s: %s", code, e)
            failed.append(code)
    return total, failed


def run_daily_sync() -> None:
    """Entry point for the 17:30 daily-K sync job (runs in a background thread).

    Pulls the most recent pool snapshot's codes and syncs the last
    ``_LOOKBACK_DAYS`` calendar days for **every** code in the snapshot
    (full market). Codes that fail are recorded in
    :data:`_last_run_failed_codes` for the 23:00 retry job.
    """
    global _last_run_failed_codes

    # Local imports keep module import side-effect-free (no DB/session at
    # import time) and avoid a circular ref with app.core.database.
    from app.core.database import SessionLocal
    from app.models.stock import StockPool

    logger.info("daily sync job started (full market)")
    db = SessionLocal()
    try:
        latest_sp = db.execute(
            select(func.max(StockPool.trade_date)).select_from(StockPool)
        ).scalar()
        if latest_sp is None:
            logger.warning("daily sync: stock_pool empty, nothing to sync")
            _last_run_failed_codes = []
            return
        codes = (
            db.execute(
                select(StockPool.stock_code).where(
                    StockPool.trade_date == latest_sp
                )
            )
            .scalars()
            .all()
        )
        end = date.today().strftime("%Y%m%d")
        start = (date.today() - timedelta(days=_LOOKBACK_DAYS)).strftime(
            "%Y%m%d"
        )
        total, failed = _sync_codes(db, codes, start, end)
        _last_run_failed_codes = failed
        logger.info(
            "daily sync job done: %d rows, %d codes failed (will retry at 23:00)",
            total,
            len(failed),
        )
    except Exception:  # noqa: BLE001 - a scheduler job must never crash the thread
        # Without this, a DB/network storm (e.g. port exhaustion) propagates to
        # APScheduler and can freeze the scheduler thread — observed freezing
        # all subsequent jobs for 20+ hours. Catch, log, let the next run retry.
        logger.exception("daily sync job failed")
    finally:
        db.close()


def _today_looks_incomplete() -> bool:
    """Whether today's daily-K row count is far below the recent baseline.

    Used by the 23:00 retry as a self-check that survives restarts: the
    in-memory failure list is wiped by a process restart, but a partial 17:30
    run (observed 2026-08-14: 1546 of ~4168 codes) still leaves today
    under-filled, and that is visible in the data itself.

    Returns False when there is nothing to compare against (no prior settled
    days). On a weekday holiday this reports True and the retry fires a full
    sync that writes nothing — the same waste the 17:30 job already incurs
    on holidays, accepted for simplicity.
    """
    from app.core.database import SessionLocal
    from app.data.backfill import _COMPLETENESS_RATIO, settled_counts

    db = SessionLocal()
    try:
        today = date.today()
        counts = settled_counts(db)
        today_count = counts.get(today, 0)
        prior = [c for d, c in counts.items() if d < today]
        if not prior:
            return False
        baseline = max(prior)
        return today_count < _COMPLETENESS_RATIO * baseline
    finally:
        db.close()


def run_daily_sync_retry() -> None:
    """Entry point for the 23:00 retry job.

    First a restart-proof self-check: if today's settled-row count is far
    below the recent baseline, the 17:30 run (or the process) died partway —
    re-run the FULL sync; the per-code upsert is idempotent so this only
    costs the redundant fetches.

    Otherwise, replays only the codes recorded in
    :data:`_last_run_failed_codes` from the 17:30 run. A no-op (just logs)
    when there were no failures. The failure list is cleared after the run
    regardless of outcome, so a code that fails twice is not retried a third
    time automatically — it will be picked up by the next day's full 17:30
    run.
    """
    global _last_run_failed_codes

    try:
        if _today_looks_incomplete():
            logger.warning(
                "retry sync: today's daily-K looks incomplete vs recent "
                "baseline — re-running the full 17:30 sync"
            )
            run_daily_sync()
            return
    except Exception:  # noqa: BLE001 - self-check must not kill the replay below
        logger.exception("retry sync: completeness self-check failed")

    if not _last_run_failed_codes:
        logger.info("retry sync skipped: no failed codes from the 17:30 run")
        return

    from app.core.database import SessionLocal

    codes = _last_run_failed_codes
    logger.info("retry sync started: %d failed codes", len(codes))
    db = SessionLocal()
    try:
        end = date.today().strftime("%Y%m%d")
        start = (date.today() - timedelta(days=_LOOKBACK_DAYS)).strftime(
            "%Y%m%d"
        )
        total, still_failing = _sync_codes(db, codes, start, end)
        logger.info(
            "retry sync done: %d rows recovered, %d codes still failing",
            total,
            len(still_failing),
        )
    except Exception:  # noqa: BLE001 - same rationale as run_daily_sync
        logger.exception("retry sync job failed")
    finally:
        db.close()
        _last_run_failed_codes = []


def init_scheduler() -> BackgroundScheduler:
    """Create (if needed) and start the background scheduler.

    Idempotent: calling twice returns the same scheduler instance.

    Registered jobs (all weekdays, Asia/Shanghai):
    - ``daily_k_sync``: 17:30 (V1)
    - ``daily_k_sync_retry``: 23:00 — replays only the codes that failed in
      the 17:30 run; a no-op when there were no failures.
    - V1.5 data jobs run after the daily-K sync so their inputs exist.
      Each V1.5 job delegates to :func:`admin_service.run_task` so the run is
      recorded in ``sa_admin_task_log`` (same path as a manual admin trigger).
    """
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    # ``misfire_grace_time`` is generous on purpose: if a previous run (or the
    # startup back-fill) clogs the single ThreadPoolExecutor, a cron firing may
    # not be picked up until minutes later. The default 1s grace would then
    # discard it as MISSED — which is how we silently lost an entire day's
    # daily-K sync. 10 min tolerates a slow prior job without re-running stale
    # ones hours later (``coalesce=True`` still collapses the backlog to 1).
    sched = BackgroundScheduler(timezone="Asia/Shanghai")
    sched.add_job(
        run_daily_sync,
        CronTrigger(hour=17, minute=30, day_of_week="mon-fri"),
        id="daily_k_sync",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=600,
    )
    sched.add_job(
        run_daily_sync_retry,
        CronTrigger(hour=23, minute=0, day_of_week="mon-fri"),
        id="daily_k_sync_retry",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=600,
    )
    # V1.5 jobs — registered via admin_service so they share the logging path.
    # (task_name, hour, minute)
    _v15_jobs = [
        ("pool_sync", 16, 25),                # universe refresh, before everything that reads it
        ("index_sync", 16, 35),              # indices via Tencent source, stable
        ("sentiment_sync", 16, 45),          # after daily_k_sync
        ("north_flow_sync", 17, 0),
        ("money_flow_detail_sync", 17, 5),
        ("sector_sync", 17, 10),
        ("dragon_tiger_sync", 18, 0),        # dragon-tiger publishes ~17:30
        # V2 agents
        ("market_agent_sync", 18, 10),        # after data is settled
        ("review_agent_sync", 18, 20),
    ]
    for name, h, m in _v15_jobs:
        sched.add_job(
            _run_admin_task,
            CronTrigger(hour=h, minute=m, day_of_week="mon-fri"),
            args=[name],
            id=name,
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=600,
        )
    # Surface misfires/exceptions to the log explicitly (APScheduler otherwise
    # only emits a generic "was missed" line) so we can see *why* a job skipped.
    sched.add_listener(
        _on_scheduler_event,
        EVENT_JOB_MISSED | EVENT_JOB_ERROR | EVENT_JOB_EXECUTED,
    )
    _scheduler = sched
    sched.start()
    logger.info("scheduler started")
    return sched


def _run_admin_task(task_name: str) -> None:
    """Run a V1.5 task through admin_service so it gets logged."""
    from app.services import admin_service

    try:
        admin_service.run_task(task_name, triggered_by="scheduler")
    except Exception:  # noqa: BLE001 - a scheduler job must never crash the thread
        logger.exception("scheduled task %s failed", task_name)


def shutdown_scheduler() -> None:
    """Shut down the scheduler if it is running; no-op otherwise."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
