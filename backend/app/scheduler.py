"""APScheduler job registration (V2.5: topology-driven).

The cron job list is generated from ``pipeline_service.PIPELINE_TOPOLOGY`` —
the single source of truth shared with the run/step bookkeeping, the 08:00
missing-step patrol and the admin pipeline view. Every topology step executes
through ``admin_service.run_task`` so each firing lands in
``sa_admin_task_log`` AND in its pipeline step row (scheduler, retry and
manual triggers share one path). :func:`_cron_job_specs` is the pure manifest
the topology-consistency test anchors on.

Jobs outside the topology (registered explicitly):
- ``quality_check``   weekdays 08:00 (guarded by ``quality_check_enabled``);
- ``history_backfill_tick`` / ``kline_rebuild_tick``  interval polling jobs
  (per-day summary row in the task log, see admin_service.log_daily_summary);
- the startup back-fill thread (``app.main`` lifespan).

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
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import func, select

from app.core.config import settings

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


def get_scheduler() -> BackgroundScheduler | None:
    """The running scheduler, if initialized (pipeline retries need it)."""
    return _scheduler


# Look-back window for the incremental pull.
_LOOKBACK_DAYS = 7

# Cached A-share trading calendar (set of dates), loaded once per process.
# ``None`` means "not loaded / load failed" — callers then fall back to the
# weekday-only judgment. A calendar miss only wastes one sync run, while
# wrongly skipping a real trading day would lose a day of data.
_trade_cal: set[date] | None = None
_trade_cal_loaded = False


def _load_trade_calendar() -> set[date] | None:
    """Load the trading calendar once per process via akshare/sina.

    Kept lazy and failure-tolerant: a network hiccup at load time leaves
    ``_trade_cal`` as ``None`` and the callers degrade to weekday-only.
    """
    global _trade_cal, _trade_cal_loaded
    if _trade_cal_loaded:
        return _trade_cal
    _trade_cal_loaded = True
    try:
        from app.data.akshare_client import fetch_trade_calendar

        _trade_cal = set(fetch_trade_calendar())
        logger.info("trade calendar loaded: %d dates", len(_trade_cal))
    except Exception as e:  # noqa: BLE001 - calendar is an optimization, not a requirement
        logger.warning("trade calendar unavailable (%s); weekday-only judgment", e)
    return _trade_cal


def _is_trade_day(d: date) -> bool:
    """Whether ``d`` is an A-share trading day.

    Weekends are always False. Holidays (non-trading weekdays) are False only
    when the calendar loaded — on calendar failure weekdays optimistically
    count as trading days (see :func:`_load_trade_calendar` for the tradeoff).
    """
    if d.weekday() >= 5:
        return False
    cal = _load_trade_calendar()
    if cal is None:
        return True
    return d in cal

# Codes that failed in the 17:30 main run, replayed by the 23:00 retry job.
# Process-local state — a restart clears it, which is safe: with no failure
# record the retry is a no-op and the next 17:30 run still covers everything.
_last_run_failed_codes: list[str] = []


def _sync_codes(db, codes, start: str, end: str) -> tuple[int, list[str]]:
    """Sync ``codes`` over ``[start, end]`` and return ``(rows, failed)``.

    Shared by the 17:30 main run and the 23:00 retry. A failure on one code
    logs an error but does not abort the run; the failing code is collected
    into the returned ``failed`` list so the caller can replay it.

    V2.1 write targets (spec-004 §3.3): the legacy ``daily_prices`` path runs
    while ``kline_source="legacy"``; the raw-store path
    (``sync_kline.sync_one_stock_v2``) runs once ``kline_rebuild_enabled``
    opens the migration window (dual-write) and becomes the ONLY path after
    the ``kline_source="v2"`` cutover. Each path fails independently — a code
    is marked failed if any attempted write failed.
    """
    from app.data import sync_daily, sync_kline

    write_legacy = settings.kline_source == "legacy"
    write_v2 = settings.kline_source == "v2" or settings.kline_rebuild_enabled

    total = 0
    failed: list[str] = []
    for code in codes:
        code_failed = False
        if write_legacy:
            try:
                total += sync_daily.sync_one_stock(db, code, start, end)
            except Exception as e:  # noqa: BLE001 - log and continue per code
                logger.error("sync failed for %s: %s", code, e)
                code_failed = True
        if write_v2:
            try:
                total += sync_kline.sync_one_stock_v2(db, code, start, end)
            except Exception as e:  # noqa: BLE001
                logger.error("v2 sync failed for %s: %s", code, e)
                code_failed = True
        if code_failed:
            failed.append(code)
    return total, failed


def _do_daily_sync() -> tuple[int, list[str]]:
    """Raising core of the 17:30 daily-K sync (V2.5 收口).

    Unlike the historical catch-all wrapper, fatal errors PROPAGATE — the
    admin-task path needs the failure to reach ``sa_admin_task_log`` / the
    pipeline step row instead of silently logging ``success``. Per-code
    failures stay non-fatal (replayed at 23:00); only a TOTAL failure (every
    code failed, e.g. a WAF ban) raises. Returns ``(rows, failed_codes)``.
    """
    global _last_run_failed_codes

    from app.core.database import SessionLocal
    from app.models.stock import StockPool

    # Non-trading days (weekend/holiday) have nothing to pull; a full-market
    # run would only burn WAF goodwill against the kline hosts. Observed
    # 2026-08-29: the weekend retry misfired into a pointless full sync that
    # stacked on the startup backfill and accelerated the 501 ban.
    today = date.today()
    if not _is_trade_day(today):
        logger.info("daily sync skipped: %s is not a trading day", today)
        _last_run_failed_codes = []
        return 0, []

    logger.info("daily sync job started (full market)")
    db = SessionLocal()
    try:
        latest_sp = db.execute(
            select(func.max(StockPool.trade_date)).select_from(StockPool)
        ).scalar()
        if latest_sp is None:
            logger.warning("daily sync: stock_pool empty, nothing to sync")
            _last_run_failed_codes = []
            return 0, []
        codes = (
            db.execute(
                select(StockPool.stock_code).where(
                    StockPool.trade_date == latest_sp
                )
            )
            .scalars()
            .all()
        )
        end = today.strftime("%Y%m%d")
        start = (today - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y%m%d")
        total, failed = _sync_codes(db, codes, start, end)
        _last_run_failed_codes = failed
        logger.info(
            "daily sync job done: %d rows, %d codes failed (will retry at 23:00)",
            total,
            len(failed),
        )
        if codes and failed and len(failed) == len(codes):
            raise RuntimeError(
                f"daily_k_sync: all {len(codes)} codes failed (source outage?)"
            )
        return total, failed
    finally:
        db.close()


def run_daily_sync() -> None:
    """Entry point for the 17:30 daily-K sync job (runs in a background thread).

    Catch-all wrapper around :func:`_do_daily_sync` kept for direct callers
    (startup back-fill compat, tests). The scheduled path goes through
    ``admin_service.run_task("daily_k_sync")`` → the raising core, so the
    outcome lands in the task log and the pipeline step row.
    """
    try:
        _do_daily_sync()
    except Exception:  # noqa: BLE001 - a scheduler job must never crash the thread
        # Without this, a DB/network storm (e.g. port exhaustion) propagates to
        # APScheduler and can freeze the scheduler thread — observed freezing
        # all subsequent jobs for 20+ hours. Catch, log, let the next run retry.
        logger.exception("daily sync job failed")


def _today_looks_incomplete() -> bool:
    """Whether today's daily-K row count is far below the recent baseline.

    Used by the 23:00 retry as a self-check that survives restarts: the
    in-memory failure list is wiped by a process restart, but a partial 17:30
    run (observed 2026-08-14: 1546 of ~4168 codes) still leaves today
    under-filled, and that is visible in the data itself.

    Returns False when there is nothing to compare against (no prior settled
    days), and False on non-trading days (weekend/holiday: a zero row count
    is the expected outcome there, not a partial run — observed 2026-08-29
    misfiring a full weekend sync that only fed the WAF).
    """
    from app.core.database import SessionLocal
    from app.data.backfill import _COMPLETENESS_RATIO, settled_counts

    db = SessionLocal()
    try:
        today = date.today()
        if not _is_trade_day(today):
            return False
        counts = settled_counts(db)
        today_count = counts.get(today, 0)
        prior = [c for d, c in counts.items() if d < today]
        if not prior:
            return False
        baseline = max(prior)
        return today_count < _COMPLETENESS_RATIO * baseline
    finally:
        db.close()


def _do_daily_sync_retry() -> tuple[int, str]:
    """Raising core of the 23:00 compensation job (V2.5 收口).

    Returns ``(rows, mode)`` where mode is ``full_resync`` (the completeness
    self-check fired), ``replay`` (failed-code replay) or ``noop``. Fatal
    errors propagate (admin-task path); a crashing self-check falls through
    to the replay, same as before.
    """
    global _last_run_failed_codes

    try:
        if _today_looks_incomplete():
            logger.warning(
                "retry sync: today's daily-K looks incomplete vs recent "
                "baseline — re-running the full 17:30 sync"
            )
            rows, _ = _do_daily_sync()
            return rows, "full_resync"
    except Exception:  # noqa: BLE001 - self-check must not kill the replay below
        logger.exception("retry sync: completeness self-check failed")

    if not _last_run_failed_codes:
        logger.info("retry sync skipped: no failed codes from the 17:30 run")
        return 0, "noop"

    from app.core.database import SessionLocal

    codes = _last_run_failed_codes
    logger.info("retry sync started: %d failed codes", len(codes))
    db = SessionLocal()
    try:
        end = date.today().strftime("%Y%m%d")
        start = (date.today() - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y%m%d")
        total, still_failing = _sync_codes(db, codes, start, end)
        logger.info(
            "retry sync done: %d rows recovered, %d codes still failing",
            total,
            len(still_failing),
        )
        return total, "replay"
    finally:
        db.close()
        _last_run_failed_codes = []


def run_daily_sync_retry() -> None:
    """Catch-all wrapper of the 23:00 compensation job (kept for tests)."""
    try:
        _do_daily_sync_retry()
    except Exception:  # noqa: BLE001 - same rationale as run_daily_sync
        logger.exception("retry sync job failed")


# ---------------------------------------------------------------------------
# Cron job manifest (pure) + scheduler bootstrap
# ---------------------------------------------------------------------------


def _cron_job_specs() -> list[dict]:
    """Declarative cron-job manifest — no scheduler instance needed.

    The topology-consistency test asserts that the scheduler registers exactly
    these ids. Topology steps carry the admin ``task_name`` to execute, which
    may differ from the job id for variants (the uncapped nightly finance run
    books into the ``finance_sync`` step via the alias mapping).
    """
    from app.services.pipeline_service import PIPELINE_TOPOLOGY

    def _misfire(day_of_week: str) -> int:
        # Weekend jobs have a longer grace window (nothing else fires around
        # them); daily data jobs keep the tight 10-min window.
        return 3600 if day_of_week in ("sat", "sun") else 600

    specs: list[dict] = []
    for s in PIPELINE_TOPOLOGY:
        task_name = (
            "finance_sync_nightly" if s.step_key == "finance_sync" else s.step_key
        )
        specs.append(
            {
                "id": s.step_key,
                "task_name": task_name,
                "hour": s.hour,
                "minute": s.minute,
                "day_of_week": s.weekday,
                "misfire_grace_time": _misfire(s.weekday),
            }
        )
    # 23:00 compensation — books back into the daily_k_sync step (alias).
    specs.append(
        {
            "id": "daily_k_sync_retry",
            "task_name": "daily_k_sync_retry",
            "hour": 23,
            "minute": 0,
            "day_of_week": "mon-fri",
            "misfire_grace_time": 600,
        }
    )
    if settings.quality_check_enabled:
        specs.append(
            {
                "id": "quality_check",
                "task_name": "quality_check",
                "hour": 8,
                "minute": 0,
                "day_of_week": "mon-fri",
                "misfire_grace_time": 600,
            }
        )
    return specs


def init_scheduler() -> BackgroundScheduler:
    """Create (if needed) and start the background scheduler.

    Idempotent: calling twice returns the same scheduler instance. Every cron
    job fires :func:`_run_admin_task` so its run is recorded in
    ``sa_admin_task_log`` AND the pipeline step row (same path as a manual
    admin trigger). The executor pool size is explicit (V2.5): the historical
    implicit default was 10 threads.
    """
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    # ``misfire_grace_time`` is generous on purpose: if a previous run (or the
    # startup back-fill) clogs the thread pool, a cron firing may not be picked
    # up until minutes later. The default 1s grace would then discard it as
    # MISSED — which is how we silently lost an entire day's daily-K sync.
    # 10 min tolerates a slow prior job without re-running stale ones hours
    # later (``coalesce=True`` still collapses the backlog to 1).
    sched = BackgroundScheduler(
        timezone="Asia/Shanghai",
        executors={"default": ThreadPoolExecutor(settings.scheduler_pool_size)},
    )
    for spec in _cron_job_specs():
        sched.add_job(
            _run_admin_task,
            CronTrigger(
                hour=spec["hour"],
                minute=spec["minute"],
                day_of_week=spec["day_of_week"],
            ),
            args=[spec["task_name"]],
            id=spec["id"],
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=spec["misfire_grace_time"],
        )
    # Multi-year history back-fill — low-rate polling (anti-ban profile, see
    # app.data.history_backfill). ``jitter`` de-synchronises the tick from any
    # other periodic work; the tick itself skips the 17:15–18:45 daily-sync
    # quiet window and self-disables once every stock reaches its target.
    if settings.history_backfill_enabled:
        from app.data.history_backfill import tick as history_tick

        sched.add_job(
            history_tick,
            IntervalTrigger(
                minutes=settings.history_poll_minutes,
                jitter=max(60, settings.history_poll_minutes * 12),
            ),
            id="history_backfill_tick",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=300,
            max_instances=1,
        )
        logger.info(
            "history backfill polling job registered (every %d min)",
            settings.history_poll_minutes,
        )
    # V2.1 raw-store re-ingest — same low-rate polling profile as the history
    # back-fill (own quiet-window check, circuit breaker, priority queue for
    # the contaminated list). Off until the migration window opens
    # (kline_rebuild_enabled) and self-drains once every stock is done.
    if settings.kline_rebuild_enabled:
        from app.data.kline_rebuild import tick as kline_rebuild_tick

        sched.add_job(
            kline_rebuild_tick,
            IntervalTrigger(
                minutes=settings.kline_rebuild_poll_minutes,
                jitter=max(60, settings.kline_rebuild_poll_minutes * 12),
            ),
            id="kline_rebuild_tick",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=300,
            max_instances=1,
        )
        logger.info(
            "kline rebuild polling job registered (every %d min)",
            settings.kline_rebuild_poll_minutes,
        )
    # Surface misfires/exceptions to the log explicitly (APScheduler otherwise
    # only emits a generic "was missed" line) so we can see *why* a job skipped.
    sched.add_listener(
        _on_scheduler_event,
        EVENT_JOB_MISSED | EVENT_JOB_ERROR | EVENT_JOB_EXECUTED,
    )
    _scheduler = sched
    sched.start()
    logger.info(
        "scheduler started (%d cron jobs, pool=%d)",
        len(_cron_job_specs()),
        settings.scheduler_pool_size,
    )
    return sched


def _run_admin_task(task_name: str) -> None:
    """Run a scheduled task through admin_service so it gets logged."""
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
