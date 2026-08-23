"""Startup gap detection + back-fill for daily-K data.

Problem this solves: the scheduled ``daily_k_sync`` job only fires when the
backend process is alive at 17:30 on a trading day. If the host was asleep /
off / the service was down, those runs are missed and ``daily_prices`` falls
behind. This module detects that gap on startup and catches up.

It does NOT depend on the scheduler being accurate — it runs once at startup
(in a background thread, so it never blocks serving requests) and reconciles
``daily_prices`` against the current date.

Trading-day awareness: A-share market trades Mon-Fri. We treat weekdays as
candidate trading days; holidays are tolerated because the per-stock sync is a
no-op when the upstream has no bar for that date (akshare returns empty).

Completeness awareness: a day counts as settled only when its settled-row
count is within :data:`_COMPLETENESS_RATIO` of the recent baseline. A partial
run (e.g. 2026-08-14: 17:30 sync died after ~1500 of ~4200 codes) leaves
settled bars on the latest date, which used to fool ``MAX(trade_date)``-only
gap detection into "already up to date" — the partial day is now re-synced
like any other gap (the upsert is idempotent).
"""

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data import sync_daily

logger = logging.getLogger(__name__)

# Don't attempt to back-fill more than this many calendar days in one go, even
# if the gap looks huge (e.g. a fresh DB would otherwise try years). The daily-K
# source only serves recent history cheaply anyway.
MAX_BACKFILL_DAYS = 30

# A settled day is "incomplete" when its settled-row count falls below this
# fraction of the recent baseline (the max settled count over the last few
# dates). Market-wide day-over-day drift is a few ‰ (listings/suspensions), so
# 0.9 cleanly separates "a sync run died halfway" (observed 37%) from noise.
_COMPLETENESS_RATIO = 0.9

# "Today" only participates in gap detection from this local hour on — before
# the session settles there is nothing to fetch, and re-syncing would upsert
# half-day bars. The main scheduled sync runs at 17:30.
_TODAY_ELIGIBLE_HOUR = 17

# How many recent dates to look at when computing the completeness baseline.
_COUNT_WINDOW = 15


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


def settled_counts(db: Session, limit: int = _COUNT_WINDOW) -> dict[date, int]:
    """Settled row counts (``pct_change`` non-NULL) for the recent dates.

    :return: ``{trade_date: row_count}`` for the ``limit`` most recent dates
        that have settled bars, ascending order irrelevant (dict).
    """
    from app.models.stock import DailyPrice

    rows = db.execute(
        select(DailyPrice.trade_date, func.count())
        .where(DailyPrice.pct_change.is_not(None))
        .group_by(DailyPrice.trade_date)
        .order_by(DailyPrice.trade_date.desc())
        .limit(limit)
    ).all()
    return dict(rows)


def detect_gap(
    db: Session, today: date | None = None, now: datetime | None = None
) -> list[date]:
    """Return the list of trading days (weekdays) missing from ``daily_prices``.

    Compares :func:`latest_complete_trade_date` against ``today`` (default:
    real today). The window starts right after the latest complete date —
    or AT the earliest recent date whose settled-row count is abnormally low
    (:data:`_COMPLETENESS_RATIO` of the baseline), i.e. a partially-synced
    day is re-synced. Weekdays strictly after the start and up to the cap are
    returned, capped at :data:`MAX_BACKFILL_DAYS`.

    "Today" is only included from :data:`_TODAY_ELIGIBLE_HOUR` (17:00) local
    time on — before the session settles there is nothing to fetch, and an
    early sync would upsert half-day bars.

    :param today: override the reference date (tests).
    :param now: override the wall clock for the 17:00 eligibility rule (tests).
    :return: ascending list of dates to back-fill. Empty if already up to date
        or if there is no baseline data yet (caller decides whether to do an
        initial load — back-filling can't infer a start otherwise).
    """
    today = today or date.today()
    now = now or datetime.now()
    latest = latest_complete_trade_date(db)
    if latest is None:
        # No baseline: nothing to back-fill against. An initial full load is a
        # separate concern (would need a start date); return empty here.
        logger.info("backfill: no baseline data, skipping gap detection")
        return []

    # Completeness: recent dates at/below ``latest`` whose settled count is
    # far under the baseline mark a truncated sync run — re-fill from the
    # earliest such date. Dates after ``latest`` are handled by the forward
    # window below and don't participate.
    counts = {d: c for d, c in settled_counts(db).items() if d <= latest}
    start = latest + timedelta(days=1)
    if counts:
        baseline = max(counts.values())
        partial = sorted(
            d for d, c in counts.items() if c < _COMPLETENESS_RATIO * baseline
        )
        if partial:
            start = partial[0]
            logger.info(
                "backfill: %d recent day(s) look partially synced "
                "(earliest %s: %d rows vs baseline %d)",
                len(partial), partial[0], counts[partial[0]], baseline,
            )

    cap = today if now.hour >= _TODAY_ELIGIBLE_HOUR else today - timedelta(days=1)
    cap = min(cap, latest + timedelta(days=MAX_BACKFILL_DAYS))
    missing: list[date] = []
    cur = start
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
    # Circuit-breaker: a burst of consecutive failures usually means the
    # network/DB is down (observed: macOS port exhaustion, ``Errno 49``).
    # Continuing to fire 4000+ fetches in that state only makes it worse and
    # can drag the whole process — including the scheduler — down with it.
    # Bail out early; the next process restart will retry the same window.
    consecutive_failures = 0
    _CIRCUIT_BREAKER = 20
    for code in codes:
        try:
            total += sync_daily.sync_one_stock(db, code, start, end)
            consecutive_failures = 0
        except Exception as e:  # noqa: BLE001 - per-code resilience
            failures += 1
            consecutive_failures += 1
            if failures <= 5:
                logger.error("backfill: sync failed for %s: %s", code, e)
            if consecutive_failures >= _CIRCUIT_BREAKER:
                logger.error(
                    "backfill: %d consecutive failures, aborting run "
                    "(network/DB likely down) — will retry on next startup",
                    consecutive_failures,
                )
                break
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
