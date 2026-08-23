"""Daily ``stock_pool`` snapshot sync (whole A-share market, one request).

``stock_pool`` is the code universe for the daily-K sync/backfill AND the
stock-list UI. Until 2026-06-19 it was populated by an external pipeline with
no writer in this codebase; a stale universe silently drops every listing
since June from the daily syncs. This module writes one snapshot per day from
eastmoney's whole-market spot table (a single request).

``industry`` is not served by the spot table — each new snapshot inherits it
per code from the most recent prior snapshot (industries change rarely; new
listings stay NULL until an explicit industry refresh exists).

Re-running on the same day REPLACES that day's snapshot (delete + insert),
so the scheduled job is idempotent.
"""

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.data import akshare_client
from app.models.stock import StockPool

logger = logging.getLogger(__name__)


def _exchange(code: str) -> str:
    """Exchange tag from the code's first digit (same rule as Tencent's)."""
    head = code[0]
    if head in ("6", "9", "5"):
        return "sh"
    if head == "8":
        return "bj"
    return "sz"


def _dec(v) -> Decimal | None:
    """float → Decimal for the Numeric columns; None for NaN/unparseable."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return Decimal(str(v))


def _prior_industries(db: Session, before: date) -> dict[str, str]:
    """``{stock_code: industry}`` from the most recent snapshot before ``before``.

    Only codes with a non-NULL industry participate; the newest snapshot that
    has one wins.
    """
    latest = db.execute(
        select(func.max(StockPool.trade_date)).where(
            StockPool.trade_date < before, StockPool.industry.is_not(None)
        )
    ).scalar()
    if latest is None:
        return {}
    rows = db.execute(
        select(StockPool.stock_code, StockPool.industry).where(
            StockPool.trade_date == latest
        )
    ).all()
    return dict(rows)


def write_pool_snapshot(
    db: Session,
    spots: list[dict],
    pool_name: str = "default",
    trade_date: date | None = None,
) -> int:
    """Replace ``pool_name``'s snapshot for ``trade_date`` with ``spots``.

    :param spots: rows from :func:`akshare_client.fetch_spot_table`.
    :return: number of rows written.
    """
    trade_date = trade_date or date.today()
    industries = _prior_industries(db, trade_date)
    rows = []
    for s in spots:
        code = s.get("stock_code")
        if not code:
            continue
        rows.append(
            StockPool(
                pool_name=pool_name,
                trade_date=trade_date,
                stock_code=code,
                stock_name=s.get("stock_name"),
                exchange=_exchange(code),
                industry=industries.get(code),
                close=_dec(s.get("close")),
                pct_change=_dec(s.get("pct_change")),
                total_mv=_dec(s.get("total_mv")),
                circ_mv=_dec(s.get("circ_mv")),
                turnover=_dec(s.get("turnover")),
                pe=_dec(s.get("pe")),
                pb=_dec(s.get("pb")),
            )
        )
    if not rows:
        return 0
    db.execute(
        delete(StockPool).where(
            StockPool.pool_name == pool_name,
            StockPool.trade_date == trade_date,
        )
    )
    db.add_all(rows)
    db.commit()
    return len(rows)


def sync_pool_snapshot(
    db: Session, pool_name: str = "default", trade_date: date | None = None
) -> int:
    """Fetch the whole-market spot table and write one day's pool snapshot.

    A failed/empty fetch writes nothing — the previous day's snapshot stays
    the active universe for the daily-K sync.

    :return: number of rows written.
    """
    trade_date = trade_date or date.today()
    spots = akshare_client.fetch_spot_table()
    if not spots:
        logger.warning("pool sync: upstream spot table empty, nothing written")
        return 0
    n = write_pool_snapshot(db, spots, pool_name=pool_name, trade_date=trade_date)
    logger.info("pool sync: wrote %d rows for %s/%s", n, pool_name, trade_date)
    return n
