"""Industry supplement sync into ``sa_stock_industry``.

``stock_pool`` is read-only by project convention (populated by an external
pipeline), so missing ``industry`` values are written here and joined in at
query time rather than back-filled into ``stock_pool``.

Fetches per-stock industry via ``ak.stock_individual_info_em`` and UPSERTs on
``uk_code(stock_code)``. Re-running is safe.
"""

import logging

from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.data import akshare_client
from app.models.market_data import SaStockIndustry

logger = logging.getLogger(__name__)


def upsert_rows(db: Session, rows: list[dict]) -> int:
    """UPSERT industry-supplement rows.

    :return: number of rows written.
    """
    valid = [r for r in rows if r.get("stock_code")]
    if not valid:
        return 0
    stmt = mysql_insert(SaStockIndustry).values(valid)
    stmt = stmt.on_duplicate_key_update(
        {"industry": stmt.inserted.industry}
    )
    db.execute(stmt)
    db.commit()
    return len(valid)


@akshare_client.retry(
    stop=akshare_client.stop_after_attempt(3),
    wait=akshare_client.wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def fetch_industry(symbol: str) -> dict | None:
    """Fetch the industry for one stock via ``ak.stock_individual_info_em``.

    :return: ``{"stock_code", "industry"}`` or None if not found. The endpoint
        returns a 2-col (item/value) frame; we pick the ``行业`` row.
    """
    akshare_client._throttle()
    df = akshare_client._with_timeout(
        akshare_client.ak.stock_individual_info_em, symbol=symbol
    )
    if df is None or df.empty:
        return None
    # frame columns: item, value — find the 行业 row.
    row = df[df["item"] == "行业"]
    industry = row["value"].iloc[0] if not row.empty else None
    return {"stock_code": symbol, "industry": str(industry) if industry is not None else None}


def sync_one_stock(db: Session, code: str) -> int:
    """Fetch + UPSERT industry for a single stock.

    :return: 1 if written, 0 otherwise.
    """
    row = fetch_industry(code)
    if row is None:
        return 0
    return upsert_rows(db, [row])
