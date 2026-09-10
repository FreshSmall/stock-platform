"""Industry mapping sync (V2.1 BP-V2.1-006): multi-level, stable codes.

Primary source: eastmoney industry boards via akshare
``stock_board_industry_cons_em`` (~86 boards × 1 request each; the board
list comes from the existing ``fetch_sector_list("industry")``). The board
code (``BKxxxx``) is the stable ``industry_code``.

Fallback seed: the platform already holds two legacy name-only sources —
``sa_stock_industry`` (个股页) and ``stock_pool.industry`` (snapshot-inherited).
When eastmoney is unreachable (observed 2026-08-31: push2 refuses connections
in this network, same issue documented for BP-V1.5-001/004/005), the map is
seeded from those with a namespaced code ``em:<name>`` so grouping works
immediately; the next successful eastmoney pass adds a properly-coded row
(the map is keyed on effective_date, so both can coexist).

Reads go through :func:`app.services.universe_service.get_industry`.
"""

import logging
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.data.akshare_client import _throttle, _with_timeout, ak
from app.models.kline import SaIndustryMap
from app.models.market_data import SaStockIndustry
from app.models.stock import StockPool

logger = logging.getLogger(__name__)

# Request pacing for the per-board cons fetch (~86 boards).
_BOARD_PAUSE_SEC = 0.6
_MAX_BOARDS = 120  # circuit breaker on runaway board lists


def _fetch_boards() -> list[dict]:
    """Eastmoney industry board list via the existing sector fetcher."""
    from app.data.akshare_client import fetch_sector_list

    return fetch_sector_list("industry") or []


def fetch_board_cons(board_name: str) -> list[str]:
    """Constituent codes of one eastmoney industry board."""
    df = _with_timeout(ak.stock_board_industry_cons_em, symbol=board_name)
    if df is None or df.empty:
        return []
    return [str(c).zfill(6) for c in df["代码"].tolist()]


def _upsert(db: Session, rows: list[dict]) -> int:
    if not rows:
        return 0
    stmt = mysql_insert(SaIndustryMap).values(rows)
    update_cols = {
        c: getattr(stmt.inserted, c)
        for c in ("industry_code", "industry_name", "effective_date")
    }
    stmt = stmt.on_duplicate_key_update(update_cols)
    db.execute(stmt)
    db.commit()
    return len(rows)


def sync_from_eastmoney(db: Session, effective: date | None = None) -> dict:
    """Full pass: every industry board's constituents → ``sa_industry_map``."""
    import time

    effective = effective or date.today()
    boards = _fetch_boards()[:_MAX_BOARDS]
    if not boards:
        logger.warning("industry map: board list empty (eastmoney unreachable?)")
        return {"boards": 0, "rows": 0}

    total_rows = 0
    ok_boards = 0
    for i, b in enumerate(boards):
        if i:
            time.sleep(_BOARD_PAUSE_SEC)
        try:
            codes = fetch_board_cons(b["sector_name"])
            rows = [
                {
                    "stock_code": c,
                    "industry_code": b["sector_code"],
                    "industry_name": b["sector_name"],
                    "industry_level": "em",
                    "effective_date": effective,
                }
                for c in codes
            ]
            total_rows += _upsert(db, rows)
            ok_boards += 1
        except Exception as e:  # noqa: BLE001 - one board failing shouldn't kill the pass
            logger.warning("industry cons failed for %s: %s", b.get("sector_name"), e)
    logger.info("industry map: %d/%d boards, %d rows", ok_boards, len(boards), total_rows)
    return {"boards": ok_boards, "rows": total_rows}


def seed_from_legacy(db: Session, effective: date | None = None) -> dict:
    """Fallback seed from the two legacy name-only industry sources.

    Only fills codes that have NO mapping row at all — never downgrades a
    code that already carries a proper BK code from a successful eastmoney
    pass.
    """
    effective = effective or date.today()

    name_by_code: dict[str, str] = {}
    for code, industry in db.execute(
        select(SaStockIndustry.stock_code, SaStockIndustry.industry)
    ).all():
        if industry:
            name_by_code[code] = industry
    if not name_by_code:
        latest_sp = db.execute(
            select(func.max(StockPool.trade_date)).select_from(StockPool)
        ).scalar()
        if latest_sp is not None:
            for code, industry in db.execute(
                select(StockPool.stock_code, StockPool.industry).where(
                    StockPool.trade_date == latest_sp
                )
            ).all():
                if industry:
                    name_by_code[code] = industry

    # Codes that already have any mapping stay untouched.
    already = set(
        db.execute(
            select(SaIndustryMap.stock_code).where(
                SaIndustryMap.stock_code.in_(list(name_by_code.keys()))
            )
        ).scalars()
    )
    rows = [
        {
            "stock_code": code,
            "industry_code": f"em:{name}",
            "industry_name": name,
            "industry_level": "em",
            "effective_date": effective,
        }
        for code, name in name_by_code.items()
        if code not in already
    ]
    n = _upsert(db, rows)
    logger.info("industry map: legacy seed %d rows (%d distinct industries)",
                n, len({r["industry_name"] for r in rows}))
    return {"rows": n}


def sync_all(db: Session) -> int:
    """Admin/scheduler entry: try eastmoney, seed from legacy when it fails."""
    result = sync_from_eastmoney(db)
    if result.get("rows", 0) == 0:
        return int(seed_from_legacy(db).get("rows", 0))
    return int(result["rows"])


def coverage(db: Session) -> float:
    """Fraction of the latest pool snapshot with an industry-mapping row."""
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
    mapped = set(
        db.execute(
            select(SaIndustryMap.stock_code).where(
                SaIndustryMap.stock_code.in_(list(pool_codes))
            )
        ).scalars()
    )
    return len(mapped) / len(pool_codes)
