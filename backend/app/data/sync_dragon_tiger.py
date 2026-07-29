"""Dragon-tiger (龙虎榜) sync into ``sa_dragon_tiger`` + ``sa_dragon_tiger_seat``.

For one trade day: fetch the listed stocks, UPSERT each into ``sa_dragon_tiger``,
then fetch top-5 buy/sell seats for each and UPSERT into ``sa_dragon_tiger_seat``.
Idempotent on the (trade_date, stock_code) and (trade_date, stock_code, side,
rank) unique keys.
"""

import logging

from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.data import akshare_client
from app.models.market_data import SaDragonTiger, SaDragonTigerSeat

logger = logging.getLogger(__name__)


def upsert_stocks(db: Session, rows: list[dict]) -> int:
    """UPSERT dragon-tiger stock rows for one day.

    :return: number of rows written.
    """
    valid = [r for r in rows if r.get("stock_code") and r.get("trade_date")]
    if not valid:
        return 0
    payload = [
        {
            "trade_date": r["trade_date"],
            "stock_code": r["stock_code"],
            "stock_name": r.get("stock_name"),
            "reason": r.get("reason"),
            "net_buy": r.get("net_buy"),
            "buy_amount": r.get("buy_amount"),
            "sell_amount": r.get("sell_amount"),
        }
        for r in valid
    ]
    stmt = mysql_insert(SaDragonTiger).values(payload)
    stmt = stmt.on_duplicate_key_update(
        {
            c: getattr(stmt.inserted, c)
            for c in ("stock_name", "reason", "net_buy", "buy_amount", "sell_amount")
        }
    )
    db.execute(stmt)
    db.commit()
    return len(payload)


def upsert_seats(
    db: Session,
    trade_date: str,
    stock_code: str,
    seats: dict,
) -> int:
    """UPSERT top-5 buy/sell seats for one stock/day.

    ``seats`` is ``{"buy": [...], "sell": [...]}`` from
    :func:`akshare_client.fetch_dragon_tiger_seats`. Each side is ranked 1..N by
    position order (akshare already returns them ranked).

    :return: number of seat rows written.
    """
    payload = []
    for side, key in ((1, "buy"), (2, "sell")):
        for rank, seat in enumerate(seats.get(key, []), start=1):
            payload.append(
                {
                    "trade_date": trade_date,
                    "stock_code": stock_code,
                    "side": side,
                    "rank": rank,
                    "seat_name": seat.get("seat_name", ""),
                    "buy_amount": seat.get("buy_amount"),
                    "sell_amount": seat.get("sell_amount"),
                    "net_amount": seat.get("net_amount"),
                    "is_institution": seat.get("is_institution", 0),
                }
            )
    if not payload:
        return 0
    stmt = mysql_insert(SaDragonTigerSeat).values(payload)
    stmt = stmt.on_duplicate_key_update(
        {
            c: getattr(stmt.inserted, c)
            for c in ("seat_name", "buy_amount", "sell_amount", "net_amount", "is_institution")
        }
    )
    db.execute(stmt)
    db.commit()
    return len(payload)


def sync_date(db: Session, trade_date: str, *, fetch_seats: bool = True) -> int:
    """Fetch + UPSERT the full dragon-tiger board (stocks + seats) for one day.

    A failure on one stock's seats logs an error but does not abort the run.

    :param trade_date: ``'YYYYMMDD'``.
    :param fetch_seats: when False, sync only the stock list (skip seat detail).
    :return: number of stock rows written.
    """
    rows = akshare_client.fetch_dragon_tiger(trade_date)
    written = upsert_stocks(db, rows)
    if fetch_seats:
        for r in rows:
            code = r.get("stock_code")
            if not code:
                continue
            try:
                seats = akshare_client.fetch_dragon_tiger_seats(code, trade_date)
                upsert_seats(db, r["trade_date"], code, seats)
            except Exception as e:  # noqa: BLE001 - log and continue per stock
                logger.error("dragon-tiger seats failed for %s: %s", code, e)
    return written
