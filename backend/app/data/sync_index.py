"""Market-index quote sync into ``sa_index_quote`` (UPSERT, idempotent).

Fetches the daily history for each major index via the Tencent source and
UPSERTs on ``uk_index_date(index_code, trade_date)``. Re-running is safe.
"""

import logging

from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.data import akshare_client
from app.models.market_data import SaIndexQuote

logger = logging.getLogger(__name__)

# Exchange-prefixed codes (avoid colliding with stock codes in daily_prices).
MAJOR_INDICES: list[tuple[str, str]] = [
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
]


def upsert_rows(db: Session, rows: list[dict]) -> int:
    """UPSERT index-quote rows.

    :return: number of rows written.
    """
    valid = [r for r in rows if r.get("index_code") and r.get("trade_date")]
    if not valid:
        return 0
    payload = [
        {
            "index_code": r["index_code"],
            "index_name": r.get("index_name"),
            "trade_date": r["trade_date"],
            "open": r.get("open"),
            "close": r.get("close"),
            "high": r.get("high"),
            "low": r.get("low"),
            "amount": r.get("amount"),
            "pct_change": r.get("pct_change"),
        }
        for r in valid
    ]
    stmt = mysql_insert(SaIndexQuote).values(payload)
    stmt = stmt.on_duplicate_key_update(
        {
            c: getattr(stmt.inserted, c)
            for c in ("index_name", "open", "close", "high", "low", "amount", "pct_change")
        }
    )
    db.execute(stmt)
    db.commit()
    return len(payload)


def sync_one(db: Session, symbol: str, index_name: str = "") -> int:
    """Fetch + UPSERT the history for one index.

    :return: number of rows written.
    """
    rows = akshare_client.fetch_index_quotes(symbol, index_name)
    return upsert_rows(db, rows)


def sync_all(db: Session) -> int:
    """Sync all major indices.

    :return: total rows written.
    """
    total = 0
    for symbol, name in MAJOR_INDICES:
        try:
            total += sync_one(db, symbol, name)
        except Exception as e:  # noqa: BLE001 - one index failing shouldn't abort others
            logger.error("index sync failed for %s: %s", symbol, e)
    return total
