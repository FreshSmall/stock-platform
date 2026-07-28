"""Incremental daily-K sync into ``daily_prices`` (UPSERT, idempotent).

Re-running on the same day is safe: rows are written with
``INSERT ... ON DUPLICATE KEY UPDATE`` keyed on the
``uk_code_date(stock_code, trade_date)`` unique index, so duplicates update
in place rather than erroring or duplicating.
"""

import logging
from datetime import date, datetime

from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.data import akshare_client, validators
from app.models.stock import DailyPrice

logger = logging.getLogger(__name__)


def _to_date(v) -> date | None:
    """Coerce a trade_date value to ``datetime.date``.

    Handles: ``date``, ``datetime``, pandas ``Timestamp`` (has ``.date()``),
    and the plain ``str`` ('YYYY-MM-DD') that akshare actually returns.
    Returns None if the value can't be parsed.
    """
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    # pandas Timestamp or str
    s = str(v)
    try:
        return date.fromisoformat(s)
    except ValueError:
        try:
            return getattr(v, "date", lambda: None)()
        except Exception:
            return None


def upsert_daily_rows(db: Session, rows: list[dict]) -> int:
    """UPSERT validated rows into ``daily_prices``.

    Rows failing :func:`validators.validate_daily_row` are dropped (and logged).
    The remainder is written via ``INSERT ... ON DUPLICATE KEY UPDATE`` on the
    ``uk_code_date(stock_code, trade_date)`` unique key, so the call is safe to
    repeat. The session is committed here.

    :return: number of rows actually written (post-validation).
    """
    valid = [r for r in rows if validators.validate_daily_row(r)]
    if not valid:
        return 0
    payload = [
        {
            "stock_code": r["stock_code"],
            "trade_date": _to_date(r["trade_date"]),
            "open": r.get("open"),
            "close": r.get("close"),
            "high": r.get("high"),
            "low": r.get("low"),
            "volume": r.get("volume"),
            "amount": r.get("amount"),
            "pct_change": r.get("pct_change"),
            "turnover": r.get("turnover"),
        }
        for r in valid
    ]
    stmt = mysql_insert(DailyPrice).values(payload)
    update_cols = {
        c: getattr(stmt.inserted, c)
        for c in (
            "open",
            "close",
            "high",
            "low",
            "volume",
            "amount",
            "pct_change",
            "turnover",
        )
    }
    # SQLAlchemy 2.0 MySQL dialect API: ``on_duplicate_key_update(**cols)``.
    stmt = stmt.on_duplicate_key_update(update_cols)
    db.execute(stmt)
    db.commit()
    return len(payload)


def sync_one_stock(
    db: Session, code: str, start_date: str, end_date: str
) -> int:
    """Fetch + validate + UPSERT for a single stock.

    :param code: 6-digit stock code.
    :param start_date: ``'YYYYMMDD'``.
    :param end_date: ``'YYYYMMDD'``.
    :return: number of rows written.
    """
    rows = akshare_client.fetch_daily_quotes(code, start_date, end_date)
    return upsert_daily_rows(db, rows)
