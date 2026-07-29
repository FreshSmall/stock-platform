"""APScheduler job registration.

Jobs:
- ``daily_k_sync``: weekdays 16:30 Asia/Shanghai → incremental daily-K sync.

The scheduler is NOT started on import (that would create side effects and
background threads during tests / collection). Call :func:`init_scheduler`
from the FastAPI lifespan in :mod:`app.main`.
"""

import logging
from datetime import date, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

# MVP safety cap: never sync more than this many codes per run during dev.
# Remove / raise once the full-market run is proven stable.
_MAX_CODES_PER_RUN = 50
# Look-back window for the incremental pull.
_LOOKBACK_DAYS = 7


def run_daily_sync() -> None:
    """Entry point for the daily-K sync job (runs in a background thread).

    Pulls the most recent pool snapshot's codes and syncs the last
    ``_LOOKBACK_DAYS`` calendar days for each (capped at
    ``_MAX_CODES_PER_RUN`` for MVP safety). A failure on one code logs an
    error but does not abort the run.
    """
    # Local imports keep module import side-effect-free (no DB/session at
    # import time) and avoid a circular ref with app.core.database.
    from app.core.database import SessionLocal
    from app.data import sync_daily
    from app.models.stock import StockPool

    logger.info("daily sync job started")
    db = SessionLocal()
    try:
        latest_sp = db.execute(
            select(func.max(StockPool.trade_date)).select_from(StockPool)
        ).scalar()
        if latest_sp is None:
            logger.warning("daily sync: stock_pool empty, nothing to sync")
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
        total = 0
        for code in codes[:_MAX_CODES_PER_RUN]:
            try:
                total += sync_daily.sync_one_stock(db, code, start, end)
            except Exception as e:  # noqa: BLE001 - log and continue per code
                logger.error("sync failed for %s: %s", code, e)
        logger.info("daily sync job done: %d rows", total)
    finally:
        db.close()


def init_scheduler() -> BackgroundScheduler:
    """Create (if needed) and start the background scheduler.

    Idempotent: calling twice returns the same scheduler instance.
    """
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    sched = BackgroundScheduler(timezone="Asia/Shanghai")
    sched.add_job(
        run_daily_sync,
        CronTrigger(hour=16, minute=30, day_of_week="mon-fri"),
        id="daily_k_sync",
        replace_existing=True,
        coalesce=True,
    )
    _scheduler = sched
    sched.start()
    logger.info("scheduler started")
    return sched


def shutdown_scheduler() -> None:
    """Shut down the scheduler if it is running; no-op otherwise."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
