"""Startup gap detection + back-fill for daily-K data.

Problem this solves: the scheduled ``daily_k_sync`` job only fires when the
backend process is alive at 16:30 on a trading day. If the host was asleep /
off / the service was down, those runs are missed and ``daily_prices`` falls
behind. This module detects that gap on startup and catches up.

It does NOT depend on the scheduler being accurate — it runs once at startup
(in a background thread, so it never blocks serving requests) and reconciles
``daily_prices`` against the current date.

Trading-day awareness: A-share market trades Mon-Fri. We treat weekdays as
candidate trading days; holidays are tolerated because the per-stock sync is a
no-op when the upstream has no bar for that date (akshare returns empty).
"""

import logging
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data import sync_daily

logger = logging.getLogger(__name__)

# Don't attempt to back-fill more than this many calendar days in one go, even
# if the gap looks huge (e.g. a fresh DB would otherwise try years). The daily-K
# source only serves recent history cheaply anyway.
MAX_BACKFILL_DAYS = 30


def latest_complete_trade_date(db: Session) -> date | None:
    """The most recent date in ``daily_prices`` that has *settled* bars.

    "Settled" = at least one row with a non-NULL ``pct_change``. An in-progress
    session has NULL pct_change (not yet computed), so we skip it — same rule as
    :func:`market_service._latest_trade_date`.
    """
    from app.models.stock import DailyPrice

    return db.execute(
        select(func.max(DailyPrice.trade_date)).where(
            DailyPrice.pct_change.is_not(None)
        )
    ).scalar()


def detect_gap(db: Session, today: date | None = None) -> list[date]:
    """Return the list of trading days (weekdays) missing from ``daily_prices``.

    Compares :func:`latest_complete_trade_date` against ``today`` (default:
    real today). Returns the weekday dates strictly after the latest complete
    date and up to (and including) today, capped at :data:`MAX_BACKFILL_DAYS`.

    :return: ascending list of dates to back-fill. Empty if already up to date
        or if there is no baseline data yet (caller decides whether to do an
        initial load — back-filling can't infer a start otherwise).
    """
    today = today or date.today()
    latest = latest_complete_trade_date(db)
    if latest is None:
        # No baseline: nothing to back-fill against. An initial full load is a
        # separate concern (would need a start date); return empty here.
        logger.info("backfill: no baseline data, skipping gap detection")
        return []

    missing: list[date] = []
    cur = latest + timedelta(days=1)
    cap = min(today, latest + timedelta(days=MAX_BACKFILL_DAYS))
    while cur <= cap:
        # weekday(): Mon=0 .. Sun=6 → trading days are 0-4.
        if cur.weekday() < 5:
            missing.append(cur)
        cur += timedelta(days=1)
    if missing:
        logger.info(
            "backfill: detected gap %s..%s (%d weekday(s))",
            missing[0], missing[-1], len(missing),
        )
    return missing


def _enumerate_pool_codes(db: Session) -> list[str]:
    """Return the stock codes from the latest ``stock_pool`` snapshot.

    Factored out so tests can mock it and stay isolated from the real 4000+
    row snapshot.
    """
    from app.models.stock import StockPool

    latest_sp = db.execute(
        select(func.max(StockPool.trade_date)).select_from(StockPool)
    ).scalar()
    if latest_sp is None:
        return []
    return (
        db.execute(
            select(StockPool.stock_code).where(StockPool.trade_date == latest_sp)
        )
        .scalars()
        .all()
    )


def backfill_daily_k(
    db: Session, missing_days: list[date] | None = None
) -> int:
    """Back-fill daily-K for the missing days across the whole stock pool.

    Fetches the freshest ``stock_pool`` snapshot's codes and syncs each over
    the gap window in ONE request per code (akshare takes a date range), so the
    cost is one network call per stock regardless of gap length. A failure on
    one code is logged and skipped — the run continues.

    NOTE: unlike the capped MVP ``run_daily_sync`` (50 codes), the back-fill
    syncs the FULL pool so the overview actually moves forward. This is slower
    (minutes for ~4000 codes) and is why it runs in a background thread.

    :param missing_days: when None, :func:`detect_gap` is called to compute it.
    :return: total rows written.
    """
    if missing_days is None:
        missing_days = detect_gap(db)
    if not missing_days:
        logger.info("backfill: daily-K already up to date, nothing to do")
        return 0

    codes = _enumerate_pool_codes(db)
    if not codes:
        logger.warning("backfill: no codes in latest stock_pool snapshot")
        return 0

    start = missing_days[0].strftime("%Y%m%d")
    end = missing_days[-1].strftime("%Y%m%d")
    logger.info(
        "backfill: syncing %d codes for %s..%s (this may take a while)",
        len(codes), start, end,
    )

    total = 0
    failures = 0
    for code in codes:
        try:
            total += sync_daily.sync_one_stock(db, code, start, end)
        except Exception as e:  # noqa: BLE001 - per-code resilience
            failures += 1
            if failures <= 5:
                logger.error("backfill: sync failed for %s: %s", code, e)
    if failures > 5:
        logger.error("backfill: ...and %d more codes failed", failures - 5)
    logger.info(
        "backfill: done, %d rows written (%d codes failed)", total, failures
    )
    return total


def backfill_on_startup() -> None:
    """Entrypoint for the startup back-fill thread.

    Opens its own session (the startup thread must not share the request
    session), detects the gap, and catches up. Swallows all errors so a
    back-fill failure never prevents the service from starting.
    """
    from app.core.database import SessionLocal

    try:
        db = SessionLocal()
        try:
            backfill_daily_k(db)
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - startup must not crash on backfill
        logger.exception("backfill: startup back-fill failed")
