"""Supplementary finance & money-flow sync into ``sa_money_flow`` / ``sa_financial_extra``.

``sa_financial_extra`` (roe/eps/revenue_growth/profit_growth per report) is
fed by :func:`akshare_client.fetch_financial_abstract` — ONE request per
stock against ``ak.stock_financial_abstract`` (the per-report indicator
alternative costs ~29 paginated requests per stock; unusable market-wide).

Progress is self-tracking — no state table needed: a code counts as filled
once it has ANY rows; codes whose latest ``updated_at`` is older than
``refresh_days`` get a rolling refresh (financial reports change quarterly,
so 120 days is ample). The scheduler job runs the full initial fill nightly
(~4600 × 0.7s ≈ 55 min, same scale as the daily-K sync); the admin task runs
one capped batch so it fits the 300s task deadline.
"""

import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.data import akshare_client
from app.models.finance import SaFinancialExtra, SaMoneyFlow
from app.models.stock import StockPool

logger = logging.getLogger(__name__)

# A run of this many consecutive fetch failures means the network/source is
# down — abort and let the next scheduled run resume (same rationale as the
# daily-K back-fill's circuit breaker).
_CIRCUIT_BREAKER = 15

# Pacing between stocks (stacks with the client's own 0.5s throttle).
_STOCK_PAUSE_SEC = 0.2


def upsert_money_flow(db: Session, rows: list[dict]) -> int:
    """UPSERT money-flow rows into ``sa_money_flow``.

    Keyed on ``uk_code_date(stock_code, trade_date)``.

    :param rows: list of ``{stock_code, trade_date, main_net_inflow}``.
    :return: rows written (0 if ``rows`` is empty).
    """
    if not rows:
        return 0
    payload = [
        {
            "stock_code": r["stock_code"],
            "trade_date": r["trade_date"],
            "main_net_inflow": r.get("main_net_inflow"),
        }
        for r in rows
    ]
    stmt = mysql_insert(SaMoneyFlow).values(payload)
    stmt = stmt.on_duplicate_key_update(
        {"main_net_inflow": stmt.inserted.main_net_inflow}
    )
    db.execute(stmt)
    db.commit()
    return len(payload)


def upsert_financial_extra(db: Session, rows: list[dict]) -> int:
    """UPSERT financial-extra rows into ``sa_financial_extra``.

    Keyed on ``uk_code_report(stock_code, report_date)``.

    :param rows: list of ``{stock_code, report_date, roe, eps,
        revenue_growth, profit_growth}``.
    :return: rows written (0 if ``rows`` is empty).
    """
    if not rows:
        return 0
    payload = [
        {
            "stock_code": r["stock_code"],
            "report_date": r["report_date"],
            "roe": r.get("roe"),
            "eps": r.get("eps"),
            "revenue_growth": r.get("revenue_growth"),
            "profit_growth": r.get("profit_growth"),
        }
        for r in rows
    ]
    stmt = mysql_insert(SaFinancialExtra).values(payload)
    update_cols = {
        c: getattr(stmt.inserted, c)
        for c in ("roe", "eps", "revenue_growth", "profit_growth")
    }
    stmt = stmt.on_duplicate_key_update(update_cols)
    db.execute(stmt)
    db.commit()
    return len(payload)


# --- financial-extra sync (drives the 4 fundamental factors) ----------------


def sync_one_stock(db: Session, code: str) -> int:
    """Fetch + UPSERT one stock's financial indicators."""
    rows = akshare_client.fetch_financial_abstract(code)
    return upsert_financial_extra(db, rows)


def _count_missing(db: Session, pool_codes: set[str]) -> int:
    filled = set(
        db.execute(
            select(SaFinancialExtra.stock_code).group_by(SaFinancialExtra.stock_code)
        ).scalars()
    )
    return len([c for c in pool_codes if c not in filled])


def _pool_codes(db: Session) -> set[str]:
    latest_sp = db.execute(
        select(func.max(StockPool.trade_date)).select_from(StockPool)
    ).scalar()
    if latest_sp is None:
        return set()
    return set(
        db.execute(
            select(StockPool.stock_code).where(StockPool.trade_date == latest_sp)
        ).scalars()
    )


def sync_all(
    db: Session,
    refresh_days: int = 120,
    stale_cap: int = 200,
    missing_cap: int | None = None,
) -> dict:
    """Sync financial extras for the codes needing it most.

    :param missing_cap: cap on first-fill codes per run — ``None`` (scheduler,
        no deadline) fills everything; the admin task passes a cap so the run
        fits the 300s task deadline.
    :return: summary dict for logging (``remaining_missing`` recomputed from
        the table AFTER the run — a circuit-breaker abort must not report 0).
    """
    pool = _pool_codes(db)
    updated = dict(
        db.execute(
            select(SaFinancialExtra.stock_code, func.max(SaFinancialExtra.updated_at))
            .group_by(SaFinancialExtra.stock_code)
        ).all()
    )
    missing = sorted(c for c in pool if c not in updated)
    cutoff = datetime.now() - timedelta(days=refresh_days)
    stale = sorted(
        (c for c in pool if c in updated and updated[c] < cutoff),
        key=lambda c: updated[c],
    )[:stale_cap]
    todo = missing if missing_cap is None else missing[:missing_cap]
    if not todo and not stale:
        logger.info("finance sync: everything up to date")
        return {"synced": 0, "rows": 0, "failed": 0, "remaining_missing": _count_missing(db, pool)}

    synced = rows_total = failed = 0
    consecutive = 0
    aborted = False
    for i, code in enumerate(todo + stale):
        if i:
            time.sleep(_STOCK_PAUSE_SEC)
        try:
            rows_total += sync_one_stock(db, code)
            synced += 1
            consecutive = 0
        except Exception as e:  # noqa: BLE001 - per-code resilience
            failed += 1
            consecutive += 1
            if failed <= 5:
                logger.error("finance sync failed for %s: %s", code, e)
            if consecutive >= _CIRCUIT_BREAKER:
                logger.error(
                    "finance sync: %d consecutive failures, aborting run", consecutive
                )
                aborted = True
                break
    remaining = _count_missing(db, pool)
    logger.info(
        "finance sync: %d codes synced (%d rows, %d failed%s), %d still missing",
        synced, rows_total, failed, ", aborted" if aborted else "", remaining,
    )
    return {
        "synced": synced,
        "rows": rows_total,
        "failed": failed,
        "aborted": aborted,
        "remaining_missing": remaining,
    }


def run_finance_sync(missing_cap: int | None = None) -> dict:
    """Scheduler entry: own session, never raises (jobs must not kill the thread)."""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        return sync_all(db, missing_cap=missing_cap)
    except Exception:  # noqa: BLE001
        logger.exception("finance sync job failed")
        return {"error": "finance sync failed"}
    finally:
        db.close()
