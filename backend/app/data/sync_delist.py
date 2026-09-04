"""Stock lifecycle sync (V2.1 BP-V2.1-004): point-in-time universe basis.

Merges two sources into ``sa_stock_lifecycle``:

* **delisted** — akshare ``stock_info_sh_delist`` (159 rows; note the 4th
  column is 暂停上市日期, the best SH publishes) and
  ``stock_info_sz_delist("终止上市公司")`` (208 rows). Verified live
  2026-08-31 against akshare 1.18.80.
* **in-market** — the latest ``stock_pool`` snapshot (codes + list_date +
  exchange + name), ``list_status='L'``.

A stock is investable on date D iff ``list_date <= D AND (delist_date IS NULL
OR delist_date > D)`` — :func:`app.services.universe_service.get_pool_asof`
consumes exactly that.
"""

import logging
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.data.akshare_client import _throttle, _with_timeout, ak
from app.models.kline import SaStockLifecycle
from app.models.stock import StockPool

logger = logging.getLogger(__name__)


def fetch_delisted() -> list[dict]:
    """Delisted A-share list from both exchanges (verified schema, see module doc).

    SH's 暂停上市日期 and SZ's 终止上市日期 both land in ``delist_date``.
    """
    out: list[dict] = []
    try:
        _throttle()
        df = _with_timeout(ak.stock_info_sh_delist)
        for _, row in df.iterrows():
            out.append(
                {
                    "stock_code": str(row["公司代码"]).zfill(6),
                    "stock_name": row.get("公司简称"),
                    "list_date": _to_date(row.get("上市日期")),
                    "delist_date": _to_date(row.get("暂停上市日期")),
                    "exchange": "SH",
                }
            )
    except Exception as e:  # noqa: BLE001 - one exchange failing shouldn't kill the other
        logger.warning("SH delist fetch failed: %s", e)
    try:
        _throttle()
        df = _with_timeout(ak.stock_info_sz_delist, symbol="终止上市公司")
        for _, row in df.iterrows():
            out.append(
                {
                    "stock_code": str(row["证券代码"]).zfill(6),
                    "stock_name": row.get("证券简称"),
                    "list_date": _to_date(row.get("上市日期")),
                    "delist_date": _to_date(row.get("终止上市日期")),
                    "exchange": "SZ",
                }
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("SZ delist fetch failed: %s", e)
    return out


def _to_date(v) -> date | None:
    if v is None:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _upsert(db: Session, rows: list[dict]) -> int:
    if not rows:
        return 0
    stmt = mysql_insert(SaStockLifecycle).values(rows)
    update_cols = {
        c: getattr(stmt.inserted, c)
        for c in ("stock_name", "exchange", "list_date", "delist_date", "list_status")
    }
    stmt = stmt.on_duplicate_key_update(update_cols)
    db.execute(stmt)
    db.commit()
    return len(rows)


def sync_lifecycle(db: Session) -> dict:
    """Full lifecycle refresh: in-market snapshot + delisted lists.

    The delisted pass runs LAST so a stock that delists after the last pool
    snapshot keeps its terminal record (an upsert would re-``L`` it only if
    a stale snapshot still contains it — the pool sync drops delisted codes
    within a day, so at worst one stale day).

    :return: summary ``{"listed": n, "delisted": m}``.
    """
    latest_sp = db.execute(
        select(func.max(StockPool.trade_date)).select_from(StockPool)
    ).scalar()
    listed_rows: list[dict] = []
    if latest_sp is not None:
        for code, name, exchange, list_date in db.execute(
            select(
                StockPool.stock_code,
                StockPool.stock_name,
                StockPool.exchange,
                StockPool.list_date,
            ).where(StockPool.trade_date == latest_sp)
        ).all():
            listed_rows.append(
                {
                    "stock_code": code,
                    "stock_name": name,
                    "exchange": exchange,
                    "list_date": list_date,
                    "delist_date": None,
                    "list_status": "L",
                }
            )
    n_listed = _upsert(db, listed_rows)

    delist_rows = [
        {**r, "list_status": "D"} for r in fetch_delisted()
    ]
    n_delisted = _upsert(db, delist_rows)
    logger.info(
        "lifecycle sync: %d listed + %d delisted rows upserted", n_listed, n_delisted
    )
    return {"listed": n_listed, "delisted": n_delisted}


def lifecycle_coverage(db: Session) -> float:
    """Fraction of the latest pool snapshot covered by the lifecycle table."""
    latest_sp = db.execute(
        select(func.max(StockPool.trade_date)).select_from(StockPool)
    ).scalar()
    if latest_sp is None:
        return 0.0
    pool_codes = set(
        db.execute(
            select(StockPool.stock_code).where(StockPool.trade_date == latest_sp)
        ).scalars()
    )
    if not pool_codes:
        return 0.0
    have = set(
        db.execute(
            select(SaStockLifecycle.stock_code).where(
                SaStockLifecycle.stock_code.in_(list(pool_codes))
            )
        ).scalars()
    )
    return len(have) / len(pool_codes)
