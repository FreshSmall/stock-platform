"""Multi-year daily-K history back-fill, driven by a low-rate polling job.

Problem: ``daily_prices`` only carries roughly the last year per stock, which
is too thin for factor research / meta-labeling. This module back-fills
``history_years`` (default 5) of history per stock — but a full-market
back-fill is ~4200 stocks × ~5 chunked requests, far too much for one run.
So it is consumed by the scheduler as a *polling tick*
(``history_backfill_tick``): every ``history_poll_minutes`` minutes, sync one
``history_batch_size`` batch of pending stocks. Gentle on the data sources,
resumable across restarts, and visible via :func:`history_progress`.

Why chunking: Tencent's kline endpoint serves "the latest N bars" and clamps
large N to ~640 (verified live 2026-08-19). The default incremental sync caps
N at 400; the history back-fill requests 640 and splits the window into
``CHUNK_DAYS`` calendar-day chunks so each request's bar estimate stays under
the clamp.

Per-stock target start: ``max(history_start, list_date)`` — a stock listed in
2023 must not be retried forever for 2021 bars it can never have. A stock is
``done`` once its earliest stored bar reaches ``target_start + GRACE_DAYS``
(grace covers listings that opened for trade a few days after ``list_date``
and long suspensions); otherwise each unsuccessful pass increments
``attempts`` and after ``MAX_ATTEMPTS`` the row is parked as ``failed``
(resettable via the ``history_backfill_reset`` admin task).

All fetches go through :func:`app.data.sync_daily.sync_one_stock`, so writes
are the same validated, idempotent upsert as the daily incremental sync.
"""

import logging
import time
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.data.sync_daily import sync_one_stock
from app.models.market_data import SaHistorySyncState
from app.models.stock import DailyPrice, StockPool

logger = logging.getLogger(__name__)

# Calendar days per fetch chunk: 400d ≈ 610 trading bars (span*7/5 + 50),
# under the Tencent server clamp of ~640 bars per request.
CHUNK_DAYS = 400

# Bar-count cap passed to the Tencent fetch (see module docstring).
MAX_BARS_PER_REQ = 640

# --- Rate profile (anti-ban). The daily incremental sync's Tencent path is
# unthrottled and survived ~4200 back-to-back requests, but the 2026-08-16
# incident showed WAF challenges after a ~2000-request burst on a related
# host. History back-fill therefore spaces its OWN requests explicitly and
# keeps bursts small: ~15 stocks × ~5 chunks ≈ 75 requests spread over ~2.5
# minutes every 10 minutes — a ~25% duty cycle with sub-1 req/s peak.
REQ_INTERVAL_SEC = 0.8   # pause between chunk requests of the same stock
STOCK_PAUSE_SEC = 1.0    # pause between stocks in a batch

# Daily-K full-market sync window to stay quiet during — stacking the history
# burst on top of the 17:30/23:00 incremental bursts doubles the pressure on
# the same endpoints for no benefit (both write the same table).
_QUIET_FROM, _QUIET_TO = 17 * 60 + 15, 18 * 60 + 45  # 17:15–18:45 local

# Earliest bar may land this many days after the target and still count as
# done — covers listing-day offsets and early suspensions.
GRACE_DAYS = 14

# Completeness gate: a stock is done only when its bar count reaches this
# fraction of the expected trading days. Earliest-bar alone misses MID-window
# holes (observed: 600519 marked done at 874 of ~1210 bars after a chunk
# failed mid-run) — those holes would never be revisited. Expected ≈
# weekdays × 0.93 (CN holidays trim ~7% off the weekday count).
_MIN_FILL_RATIO = 0.9
_HOLIDAY_TRIM = 0.93

# Failed passes before a stock is parked as ``failed``.
MAX_ATTEMPTS = 5

# Consecutive fetch failures inside one tick that abort the whole tick —
# same rationale as the startup back-fill's circuit breaker.
_CIRCUIT_BREAKER = 10


def history_start(today: date | None = None) -> date:
    """The global back-fill start date: ``history_years`` years back."""
    return (today or date.today()) - timedelta(days=365 * settings.history_years)


def target_start_for(list_date: date | None, today: date | None = None) -> date:
    """Per-stock target start: never earlier than the listing date."""
    start = history_start(today)
    if list_date is not None and list_date > start:
        return list_date
    return start


def chunk_windows(
    start: date, end: date, chunk_days: int = CHUNK_DAYS
) -> list[tuple[date, date]]:
    """Split ``[start, end]`` into ascending inclusive windows of ≤ chunk_days."""
    if end < start:
        return []
    out: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        nxt = min(cur + timedelta(days=chunk_days - 1), end)
        out.append((cur, nxt))
        cur = nxt + timedelta(days=1)
    return out


def _earliest_bar(db: Session, code: str) -> date | None:
    return db.execute(
        select(func.min(DailyPrice.trade_date)).where(
            DailyPrice.stock_code == code
        )
    ).scalar()


def _earliest_bars_bulk(
    db: Session, codes: list[str], chunk: int = 500
) -> dict[str, date]:
    """``{code: min(trade_date)}`` for ``codes`` via chunked GROUP BY queries.

    One MIN() per code round-tripped 4595 times ≈ 5 minutes on the first
    seed (observed live); the bulk form does it in ~10 indexed queries.
    """
    out: dict[str, date] = {}
    for i in range(0, len(codes), chunk):
        rows = db.execute(
            select(DailyPrice.stock_code, func.min(DailyPrice.trade_date))
            .where(DailyPrice.stock_code.in_(codes[i : i + chunk]))
            .group_by(DailyPrice.stock_code)
        ).all()
        out.update(dict(rows))
    return out


def _bar_counts_bulk(
    db: Session, codes: list[str], chunk: int = 500
) -> dict[str, int]:
    """``{code: row_count}`` for ``codes`` via chunked GROUP BY queries."""
    out: dict[str, int] = {}
    for i in range(0, len(codes), chunk):
        rows = db.execute(
            select(DailyPrice.stock_code, func.count())
            .where(DailyPrice.stock_code.in_(codes[i : i + chunk]))
            .group_by(DailyPrice.stock_code)
        ).all()
        out.update({c: int(n) for c, n in rows})
    return out


def expected_bars(start: date, end: date) -> int:
    """Approximate A-share trading days in ``[start, end]``.

    Weekdays minus ~7% for CN public holidays — an estimate by design; the
    ``_MIN_FILL_RATIO`` gate keeps it on the forgiving side.
    """
    days = max((end - start).days, 1)
    return max(int(days * 5 / 7 * _HOLIDAY_TRIM), 1)


def _is_complete(target: date, end: date, earliest: date | None, bars: int) -> bool:
    """Done-check: earliest bar reached the target AND no big mid-window holes."""
    if earliest is None:
        return False
    if earliest > target + timedelta(days=GRACE_DAYS):
        return False
    return bars >= int(_MIN_FILL_RATIO * expected_bars(target, end))


def _complete_from_own_start(
    target: date, end: date, earliest: date | None, bars: int
) -> bool:
    """Relaxed acceptance: continuous coverage measured from the stock's OWN
    first bar (not the global target).

    For codes whose ``stock_pool.list_date`` is NULL (many 2022+ listings) the
    target defaults to 5 years ago — unreachable by construction. After TWO
    independent passes both fetch the pre-target chunks and get nothing back,
    the de-facto first trade date IS ``earliest``; accepting then avoids
    burning 5 attempts × ~5 fetches per stock to park it as a false "failed".
    Mid-window holes still fail (bars are counted from ``earliest``).
    """
    if earliest is None:
        return False
    start = max(target, earliest)
    return bars >= int(_MIN_FILL_RATIO * expected_bars(start, end))


def _pool_list_dates(db: Session) -> dict[str, date | None]:
    """``{code: list_date}`` for the freshest ``stock_pool`` snapshot.

    Factored out so tests can patch it and stay isolated from the live
    4000+ row snapshot.
    """
    latest_sp = db.execute(
        select(func.max(StockPool.trade_date)).select_from(StockPool)
    ).scalar()
    if latest_sp is None:
        return {}
    rows = db.execute(
        select(StockPool.stock_code, StockPool.list_date).where(
            StockPool.trade_date == latest_sp
        )
    ).all()
    return {code: ld for code, ld in rows}


def ensure_state(db: Session, pool: dict[str, date | None] | None = None) -> int:
    """Seed ``sa_history_sync_state`` rows for pool codes not present yet.

    Newly seeded codes whose stored history already reaches the target are
    marked ``done`` immediately (no fetch). Existing rows are left untouched —
    attempts/status survive restarts. Delisted codes that drop out of the
    snapshot are left as-is (harmless).

    :param pool: ``{code: list_date}`` override (tests); the latest snapshot
        by default.
    :return: number of rows inserted.
    """
    pool = pool if pool is not None else _pool_list_dates(db)
    if not pool:
        return 0
    existing = set(
        db.execute(
            select(SaHistorySyncState.stock_code).where(
                SaHistorySyncState.stock_code.in_(list(pool.keys()))
            )
        ).scalars()
    )
    missing = [c for c in pool if c not in existing]
    if not missing:
        return 0

    earliest_map = _earliest_bars_bulk(db, missing)
    counts_map = _bar_counts_bulk(db, missing)
    today = date.today()
    inserted = 0
    for code in missing:
        target = target_start_for(pool[code])
        earliest = earliest_map.get(code)
        bars = counts_map.get(code, 0)
        done = _is_complete(target, today, earliest, bars)
        db.add(
            SaHistorySyncState(
                stock_code=code,
                target_start=target,
                earliest_bar=earliest,
                status="done" if done else "pending",
            )
        )
        inserted += 1
    db.commit()
    logger.info("history backfill: seeded %d state rows", inserted)
    return inserted


def next_pending(db: Session, limit: int) -> list[SaHistorySyncState]:
    """Pending rows with attempts left, fewest attempts first."""
    return (
        db.execute(
            select(SaHistorySyncState)
            .where(
                SaHistorySyncState.status == "pending",
                SaHistorySyncState.attempts < MAX_ATTEMPTS,
            )
            .order_by(SaHistorySyncState.attempts.asc(), SaHistorySyncState.id.asc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


def sync_stock_history(
    db: Session, code: str, start: date, end: date
) -> tuple[int, date | None]:
    """Chunked history sync for one stock; returns ``(rows_written, earliest)``.

    Sleeps ``REQ_INTERVAL_SEC`` between chunk requests — the anti-ban pacing
    documented at the top of the module.
    """
    rows = 0
    windows = chunk_windows(start, end)
    for i, (cs, ce) in enumerate(windows):
        if i:
            time.sleep(REQ_INTERVAL_SEC)
        rows += sync_one_stock(db, code, cs.strftime("%Y%m%d"), ce.strftime("%Y%m%d"),
                               max_bars=MAX_BARS_PER_REQ)
    return rows, _earliest_bar(db, code)


def run_history_batch(db: Session, batch_size: int | None = None) -> dict:
    """One polling tick's work: sync a batch of pending stocks.

    Per stock: chunked sync → re-check earliest bar → ``done`` / bump
    ``attempts`` (park as ``failed`` at ``MAX_ATTEMPTS``). A run of
    ``_CIRCUIT_BREAKER`` consecutive hard failures aborts the tick (network
    or DB down) — the next tick resumes where this one stopped.

    :return: summary dict for logging / the admin task log.
    """
    batch = next_pending(db, batch_size or settings.history_batch_size)
    if not batch:
        return {"synced": 0, "rows": 0, "done": 0, "failed": 0, "remaining": 0}

    end = date.today()
    synced = rows_total = done = failed = 0
    consecutive_errors = 0
    for idx, row in enumerate(batch):
        if idx:
            time.sleep(STOCK_PAUSE_SEC)  # anti-ban pacing between stocks
        try:
            rows, earliest = sync_stock_history(db, row.stock_code, row.target_start, end)
            rows_total += rows
            synced += 1
            consecutive_errors = 0
            bars = _bar_counts_bulk(db, [row.stock_code]).get(row.stock_code, 0)
            row.earliest_bar = earliest
            if _is_complete(row.target_start, end, earliest, bars):
                row.status = "done"
                done += 1
            elif (
                row.attempts >= 1  # 2nd+ pass: pre-target emptiness confirmed twice
                and _complete_from_own_start(row.target_start, end, earliest, bars)
            ):
                row.status = "done"
                done += 1
            else:
                # Didn't reach the target, or big mid-window holes remain
                # (a chunk failed upstream); retry fills them — the upsert
                # is idempotent.
                row.attempts += 1
                if row.attempts >= MAX_ATTEMPTS:
                    row.status = "failed"
                    failed += 1
                    row.last_error = (
                        f"incomplete after {row.attempts} attempts "
                        f"(earliest={earliest}, bars={bars})"
                    )
        except Exception as e:  # noqa: BLE001 - per-code resilience
            synced += 1
            row.attempts += 1
            row.last_error = str(e)[:500]
            if row.attempts >= MAX_ATTEMPTS:
                row.status = "failed"
                failed += 1
            consecutive_errors += 1
            logger.error("history backfill: %s failed (attempt %d): %s",
                         row.stock_code, row.attempts, e)
            if consecutive_errors >= _CIRCUIT_BREAKER:
                logger.error(
                    "history backfill: %d consecutive failures, aborting tick "
                    "(network/DB likely down)",
                    consecutive_errors,
                )
                break
        db.commit()

    remaining = db.execute(
        select(func.count()).select_from(SaHistorySyncState).where(
            SaHistorySyncState.status == "pending",
            SaHistorySyncState.attempts < MAX_ATTEMPTS,
        )
    ).scalar() or 0
    return {
        "synced": synced,
        "rows": rows_total,
        "done": done,
        "failed": failed,
        "remaining": int(remaining),
    }


def history_progress(db: Session) -> dict:
    """Status counts + completion ratio, for monitoring / the admin console."""
    rows = db.execute(
        select(SaHistorySyncState.status, func.count())
        .group_by(SaHistorySyncState.status)
    ).all()
    counts = {status: int(n) for status, n in rows}
    total = sum(counts.values())
    return {
        "total": total,
        "pending": counts.get("pending", 0),
        "done": counts.get("done", 0),
        "failed": counts.get("failed", 0),
        "progress_pct": round(counts.get("done", 0) / total * 100, 2) if total else 0.0,
    }


def reset_failed(db: Session) -> int:
    """Reset ``failed`` rows back to ``pending`` (admin task)."""
    n = db.execute(
        SaHistorySyncState.__table__.update()
        .where(SaHistorySyncState.status == "failed")
        .values(status="pending", attempts=0, last_error=None)
    ).rowcount
    db.commit()
    # Core UPDATE bypasses the identity map; without this a long-lived session
    # (``expire_on_commit=False``) would keep serving the stale ``failed`` row.
    db.expire_all()
    return int(n or 0)


def _in_quiet_window(now: datetime | None = None) -> bool:
    """Whether we're inside the daily-sync quiet window (17:15–18:45 local)."""
    minutes = (now or datetime.now()).hour * 60 + (now or datetime.now()).minute
    return _QUIET_FROM <= minutes <= _QUIET_TO


def tick() -> dict:
    """Scheduler entry: own session, seed state, run one batch, log summary."""
    from app.core.database import SessionLocal

    if not settings.history_backfill_enabled:
        return {"skipped": "disabled"}
    if _in_quiet_window():
        logger.info("history backfill tick: inside daily-sync quiet window, skipping")
        return {"skipped": "quiet_window"}

    db = SessionLocal()
    try:
        ensure_state(db)
        summary = run_history_batch(db)
        if summary.get("synced"):
            logger.info("history backfill tick: %s", summary)
        return summary
    except Exception:  # noqa: BLE001 - a scheduler job must never crash the thread
        logger.exception("history backfill tick failed")
        return {"error": "tick failed"}
    finally:
        db.close()
