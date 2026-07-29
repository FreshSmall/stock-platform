"""Four-tier (super/big/medium/small order) money-flow detail sync.

UPSERTs into ``sa_money_flow_detail`` on ``uk_code_date(stock_code, trade_date)``.
Re-running for a stock is safe.

Note: super_net + big_net together approximate the main-force net inflow stored
in ``sa_money_flow.main_net_inflow``; the two tables coexist.
"""

import logging

from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.data import akshare_client
from app.models.market_data import SaMoneyFlowDetail

logger = logging.getLogger(__name__)


def _market_of(code: str) -> str | None:
    """Infer the market (sh/sz) from a 6-digit A-share code.

    6 → sh, 0/3 → sz. Used as a convenience so callers can pass just the code.
    """
    if not code or len(code) != 6:
        return None
    if code[0] == "6":
        return "sh"
    if code[0] in ("0", "3"):
        return "sz"
    return None


def upsert_rows(db: Session, rows: list[dict]) -> int:
    """UPSERT money-flow-detail rows.

    :return: number of rows written.
    """
    valid = [r for r in rows if r.get("stock_code") and r.get("trade_date")]
    if not valid:
        return 0
    payload = [
        {
            "stock_code": r["stock_code"],
            "trade_date": r["trade_date"],
            "super_net": r.get("super_net"),
            "big_net": r.get("big_net"),
            "medium_net": r.get("medium_net"),
            "small_net": r.get("small_net"),
        }
        for r in valid
    ]
    stmt = mysql_insert(SaMoneyFlowDetail).values(payload)
    stmt = stmt.on_duplicate_key_update(
        {
            c: getattr(stmt.inserted, c)
            for c in ("super_net", "big_net", "medium_net", "small_net")
        }
    )
    db.execute(stmt)
    db.commit()
    return len(payload)


def sync_one_stock(db: Session, code: str, market: str | None = None) -> int:
    """Fetch + UPSERT money-flow detail for a single stock.

    :param market: ``'sh'``/``'sz'``; inferred from ``code`` when None.
    :return: number of rows written.
    """
    market = market or _market_of(code)
    if market is None:
        logger.warning("money-flow-detail: cannot infer market for %s", code)
        return 0
    rows = akshare_client.fetch_money_flow_detail(code, market)
    return upsert_rows(db, rows)
