"""Full-market re-ingest of ``sa_kline_daily`` (V2.1 BP-V2.1-002), polled.

Same playbook as :mod:`app.data.history_backfill` (whose constants we reuse):
a low-rate polling tick that drains a per-stock state table in small batches,
with anti-ban pacing, a daily-sync quiet window and a circuit breaker. The
two differences:

* writes go to the RAW store via :func:`app.data.sync_kline.sync_one_stock_v2`
  (raw bars + true-pct overlay + adjust-factor maintenance in one pass);
* ``priority=0`` rows (the adjustment-break contaminated list from the step1
  report, ~2,400 stocks) drain before the general population, so the PRD's
  "断裂清零" acceptance can be verified before the full market finishes.

Completion gate mirrors the history back-fill: earliest bar within
``GRACE_DAYS`` of the per-stock target AND ≥90% of expected trading days.
Progress is mirrored into ``sa_admin_task_log`` (progress_done/total) so the
admin console can chart the re-ingest live.
"""

import logging
import time
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.data import sync_kline
from app.data.history_backfill import (
    GRACE_DAYS,
    MAX_ATTEMPTS,
    REQ_INTERVAL_SEC,
    STOCK_PAUSE_SEC,
    _CIRCUIT_BREAKER,
    _in_quiet_window,
    chunk_windows,
    expected_bars,
    target_start_for,
)
from app.models.kline import SaKlineDaily, SaKlineSyncState
from app.models.market_data import SaAdminTaskLog
from app.models.stock import StockPool

logger = logging.getLogger(__name__)

# Bar cap for the raw fetch chunks (same server-side clamp as the qfq path).
MAX_BARS_PER_REQ = 640

# Fill-ratio gate (same rationale as history_backfill).
_MIN_FILL_RATIO = 0.9


def _pool_list_dates(db: Session) -> dict[str, date | None]:
    """``{code: list_date}`` for the freshest ``stock_pool`` snapshot."""
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


def _earliest_and_counts(db: Session, codes: list[str], chunk: int = 500) -> tuple[dict, dict]:
    """``({code: earliest_bar}, {code: row_count})`` over ``sa_kline_daily``."""
    earliest: dict[str, date] = {}
    counts: dict[str, int] = {}
    for i in range(0, len(codes), chunk):
        part = codes[i : i + chunk]
        rows = db.execute(
            select(SaKlineDaily.stock_code, func.min(SaKlineDaily.trade_date), func.count())
            .where(SaKlineDaily.stock_code.in_(part))
            .group_by(SaKlineDaily.stock_code)
        ).all()
        for code, lo, n in rows:
            earliest[code] = lo
            counts[code] = int(n)
    return earliest, counts


def _is_complete(target: date, end: date, earliest: date | None, bars: int) -> bool:
    if earliest is None:
        return False
    if earliest > target + timedelta(days=GRACE_DAYS):
        return False
    return bars >= int(_MIN_FILL_RATIO * expected_bars(target, end))


def ensure_state(
    db: Session,
    pool: dict[str, date | None] | None = None,
    priority_codes: list[str] | None = None,
) -> int:
    """Seed ``sa_kline_sync_state`` for pool codes not present yet.

    Codes already holding a complete raw history are seeded ``done`` directly.
    ``priority_codes`` (the contaminated list) are seeded/updated with
    ``priority=0`` and forced back to ``pending`` — re-ingest is the fix.
    """
    pool = pool if pool is not None else _pool_list_dates(db)
    if not pool:
        return 0
    priority_set = set(priority_codes or [])

    existing = {
        row.stock_code: row
        for row in db.execute(
            select(SaKlineSyncState).where(
                SaKlineSyncState.stock_code.in_(list(pool.keys()))
            )
        ).scalars()
    }
    missing = [c for c in pool if c not in existing]
    earliest, counts = _earliest_and_counts(db, missing) if missing else ({}, {})
    today = date.today()
    inserted = 0
    for code in missing:
        target = target_start_for(pool[code])
        done = _is_complete(
            target, today, earliest.get(code), counts.get(code, 0)
        )
        db.add(
            SaKlineSyncState(
                stock_code=code,
                target_start=target,
                earliest_bar=earliest.get(code),
                status="done" if done else "pending",
                priority=0 if code in priority_set else 1,
            )
        )
        inserted += 1

    # Contaminated codes that already have a state row: requeue at priority 0.
    for code in priority_set & set(existing):
        row = existing[code]
        row.priority = 0
        row.status = "pending"
        row.attempts = 0
        row.last_error = None

    db.commit()
    if inserted:
        logger.info("kline rebuild: seeded %d state rows", inserted)
    return inserted


def next_pending(db: Session, limit: int) -> list[SaKlineSyncState]:
    """Pending rows, priority 0 first, fewest attempts first."""
    return (
        db.execute(
            select(SaKlineSyncState)
            .where(
                SaKlineSyncState.status == "pending",
                SaKlineSyncState.attempts < MAX_ATTEMPTS,
            )
            .order_by(
                SaKlineSyncState.priority.asc(),
                SaKlineSyncState.attempts.asc(),
                SaKlineSyncState.id.asc(),
            )
            .limit(limit)
        )
        .scalars()
        .all()
    )


def sync_stock_history_v2(db: Session, code: str, start: date, end: date) -> int:
    """Chunked raw-history sync for one stock (with inter-chunk pacing)."""
    rows = 0
    for i, (cs, ce) in enumerate(chunk_windows(start, end)):
        if i:
            time.sleep(REQ_INTERVAL_SEC)
        rows += sync_kline.sync_one_stock_v2(
            db, code, cs.strftime("%Y%m%d"), ce.strftime("%Y%m%d"),
            max_bars=MAX_BARS_PER_REQ,
        )
    return rows


def _update_progress(log_id: int, done: int, total: int) -> None:
    """Mirror batch progress into the tick's ``sa_admin_task_log`` row."""
    from app.core.database import SessionLocal
    from datetime import datetime

    db = SessionLocal()
    try:
        db.execute(
            SaAdminTaskLog.__table__.update()
            .where(SaAdminTaskLog.id == log_id)
            .values(progress_done=done, progress_total=total)
        )
        db.commit()
    finally:
        db.close()


def run_batch(db: Session, batch_size: int | None = None, log_id: int | None = None) -> dict:
    """One tick's work: re-ingest a batch of pending stocks, gate completion."""
    batch = next_pending(db, batch_size or settings.kline_rebuild_batch_size)
    summary = {"synced": 0, "rows": 0, "done": 0, "failed": 0}
    if not batch:
        summary["remaining"] = remaining_pending(db)
        return summary

    end = date.today()
    consecutive_errors = 0
    for idx, row in enumerate(batch):
        if idx:
            time.sleep(STOCK_PAUSE_SEC)
        try:
            rows = sync_stock_history_v2(db, row.stock_code, row.target_start, end)
            summary["rows"] += rows
            summary["synced"] += 1
            consecutive_errors = 0
            (earliest, counts) = _earliest_and_counts(db, [row.stock_code])
            row.earliest_bar = earliest.get(row.stock_code)
            if _is_complete(
                row.target_start, end,
                earliest.get(row.stock_code), counts.get(row.stock_code, 0),
            ):
                row.status = "done"
                summary["done"] += 1
            else:
                row.attempts += 1
                if row.attempts >= MAX_ATTEMPTS:
                    row.status = "failed"
                    summary["failed"] += 1
                    row.last_error = (
                        f"incomplete after {row.attempts} attempts "
                        f"(earliest={earliest.get(row.stock_code)}, "
                        f"bars={counts.get(row.stock_code, 0)})"
                    )
        except Exception as e:  # noqa: BLE001 - per-code resilience
            summary["synced"] += 1
            row.attempts += 1
            row.last_error = str(e)[:500]
            if row.attempts >= MAX_ATTEMPTS:
                row.status = "failed"
                summary["failed"] += 1
            consecutive_errors += 1
            logger.error("kline rebuild: %s failed (attempt %d): %s",
                         row.stock_code, row.attempts, e)
            if consecutive_errors >= _CIRCUIT_BREAKER:
                logger.error("kline rebuild: %d consecutive failures, aborting tick",
                             consecutive_errors)
                db.commit()
                break
        db.commit()

    if log_id is not None:
        total = db.execute(
            select(func.count()).select_from(SaKlineSyncState)
        ).scalar() or 0
        done = db.execute(
            select(func.count()).select_from(SaKlineSyncState).where(
                SaKlineSyncState.status == "done"
            )
        ).scalar() or 0
        _update_progress(log_id, int(done), int(total))
    summary["remaining"] = remaining_pending(db)
    return summary


def remaining_pending(db: Session) -> int:
    return int(
        db.execute(
            select(func.count()).select_from(SaKlineSyncState).where(
                SaKlineSyncState.status == "pending",
                SaKlineSyncState.attempts < MAX_ATTEMPTS,
            )
        ).scalar() or 0
    )


def progress(db: Session) -> dict:
    """Status counts for monitoring / the admin console."""
    rows = db.execute(
        select(SaKlineSyncState.status, func.count()).group_by(SaKlineSyncState.status)
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
    n = db.execute(
        SaKlineSyncState.__table__.update()
        .where(SaKlineSyncState.status == "failed")
        .values(status="pending", attempts=0, last_error=None)
    ).rowcount
    db.commit()
    db.expire_all()
    return int(n or 0)


def enqueue_priority(db: Session, codes: list[str]) -> int:
    """(Re)queue ``codes`` at priority 0 — the contaminated-list entry point."""
    if not codes:
        return 0
    existing = {
        row.stock_code: row
        for row in db.execute(
            select(SaKlineSyncState).where(SaKlineSyncState.stock_code.in_(codes))
        ).scalars()
    }
    pool = _pool_list_dates(db)
    touched = 0
    for code in codes:
        row = existing.get(code)
        if row is None:
            db.add(
                SaKlineSyncState(
                    stock_code=code,
                    target_start=target_start_for(pool.get(code)),
                    priority=0,
                )
            )
        else:
            row.priority = 0
            row.status = "pending"
            row.attempts = 0
            row.last_error = None
        touched += 1
    db.commit()
    return touched


def tick() -> dict:
    """Scheduler entry: own session, own log row, one batch, progress update."""
    from datetime import datetime

    from app.core.database import SessionLocal

    if not settings.kline_rebuild_enabled:
        return {"skipped": "disabled"}
    if _in_quiet_window():
        return {"skipped": "quiet_window"}

    db = SessionLocal()
    log_id = None
    try:
        log = SaAdminTaskLog(
            task_name="kline_rebuild_tick",
            started_at=datetime.now(),
            status="running",
            triggered_by="scheduler",
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        log_id = log.id

        ensure_state(db)
        summary = run_batch(db, log_id=log_id)
        if summary.get("synced"):
            logger.info("kline rebuild tick: %s", summary)

        db.execute(
            SaAdminTaskLog.__table__.update()
            .where(SaAdminTaskLog.id == log_id)
            .values(
                finished_at=datetime.now(),
                status="success",
                rows_affected=summary.get("rows", 0),
                result_json=str(summary)[:2000],
            )
        )
        db.commit()
        return summary
    except Exception:  # noqa: BLE001 - a scheduler job must never crash the thread
        logger.exception("kline rebuild tick failed")
        if log_id is not None:
            db.execute(
                SaAdminTaskLog.__table__.update()
                .where(SaAdminTaskLog.id == log_id)
                .values(finished_at=datetime.now(), status="failed")
            )
            db.commit()
        return {"error": "tick failed"}
    finally:
        db.close()
