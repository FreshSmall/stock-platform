"""Intraday minute-K sync into ``sa_minute_price`` (UPSERT, idempotent).

Re-running for the same (code, period, trade_time) is safe: rows are written
with ``INSERT ... ON DUPLICATE KEY UPDATE`` keyed on
``uk_code_period_time(stock_code, period, trade_time)``.
"""

import logging
from datetime import datetime

from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.data import akshare_client
from app.models.market_data import SaMinutePrice

logger = logging.getLogger(__name__)


def _to_dt(v) -> datetime | None:
    """Coerce a trade_time value to ``datetime``.

    akshare's minute-bar ``时间`` looks like ``'2026-07-28 14:30'``.
    """
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _to_date(v) -> str | None:
    return v if isinstance(v, str) else None


def upsert_minute_rows(db: Session, rows: list[dict]) -> int:
    """UPSERT validated minute-K rows into ``sa_minute_price``.

    Rows missing ``stock_code``/``trade_time``/``period`` are dropped (logged).
    :return: number of rows written.
    """
    valid = [
        r
        for r in rows
        if r.get("stock_code") and r.get("trade_time") and r.get("period")
    ]
    if len(valid) != len(rows):
        logger.warning(
            "minute sync: dropped %d/%d rows missing key fields",
            len(rows) - len(valid),
            len(rows),
        )
    if not valid:
        return 0
    payload = [
        {
            "stock_code": r["stock_code"],
            "period": r["period"],
            "trade_date": _to_date(r.get("trade_date")),
            "trade_time": _to_dt(r["trade_time"]),
            "open": r.get("open"),
            "close": r.get("close"),
            "high": r.get("high"),
            "low": r.get("low"),
            "volume": r.get("volume"),
            "amount": r.get("amount"),
        }
        for r in valid
    ]
    # Drop rows whose trade_time failed to parse (None would violate NOT NULL).
    payload = [p for p in payload if p["trade_time"] is not None]
    if not payload:
        return 0
    stmt = mysql_insert(SaMinutePrice).values(payload)
    update_cols = {
        c: getattr(stmt.inserted, c)
        for c in ("open", "close", "high", "low", "volume", "amount", "trade_date")
    }
    stmt = stmt.on_duplicate_key_update(update_cols)
    db.execute(stmt)
    db.commit()
    return len(payload)


def sync_one_stock(db: Session, code: str, period: int = 5) -> int:
    """Fetch + UPSERT minute bars for a single stock/period.

    :return: number of rows written.
    """
    rows = akshare_client.fetch_minute_quotes(code, period=period)
    return upsert_minute_rows(db, rows)
