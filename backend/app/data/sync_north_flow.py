"""Northbound (沪深股通) flow sync into ``sa_north_flow`` (UPSERT, idempotent).

Fetches the full daily history for both channels (沪股通/深股通) and UPSERTs on
``uk_date_channel(trade_date, channel)``. Re-running is safe.
"""

import logging

from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.data import akshare_client
from app.models.market_data import SaNorthFlow

logger = logging.getLogger(__name__)


def upsert_rows(db: Session, rows: list[dict]) -> int:
    """UPSERT northbound flow rows.

    :return: number of rows written.
    """
    valid = [r for r in rows if r.get("trade_date") and r.get("channel")]
    if not valid:
        return 0
    payload = [
        {
            "trade_date": r["trade_date"],
            "channel": r["channel"],
            "net_buy": r.get("net_buy"),
            "buy_amount": r.get("buy_amount"),
            "sell_amount": r.get("sell_amount"),
        }
        for r in valid
    ]
    stmt = mysql_insert(SaNorthFlow).values(payload)
    stmt = stmt.on_duplicate_key_update(
        {
            c: getattr(stmt.inserted, c)
            for c in ("net_buy", "buy_amount", "sell_amount")
        }
    )
    db.execute(stmt)
    db.commit()
    return len(payload)


def sync_all(db: Session) -> int:
    """Fetch + UPSERT the full northbound history (both channels).

    :return: number of rows written.
    """
    rows = akshare_client.fetch_north_flow()
    return upsert_rows(db, rows)
