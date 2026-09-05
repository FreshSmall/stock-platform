"""Point-in-time universe service (V2.1 BP-V2.1-004/D2).

Sits between the research stack (IC / backtest / scoring) and the stock
universe. ``pool="current"`` (the default, unchanged V2 behaviour) reads the
latest ``stock_pool`` snapshot; ``pool="pit"`` reads ``sa_stock_lifecycle``
so historical rebalance dates include stocks that were listed THEN — even if
they delisted since. That removes the survivorship bias the step1 report
flagged (the 5,152-code pool contains zero delisted stocks).
"""

import logging
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.kline import SaStockLifecycle
from app.models.stock import DailyPrice, StockPool

logger = logging.getLogger(__name__)


def get_pool_asof(db: Session, asof: date) -> list[str]:
    """Stock codes investable on ``asof``: listed on/before it, not yet delisted.

    ``stock_pool.list_date`` is NULL for the vast majority of codes (observed
    live 2026-08-31: only 158 of ~4600 carry it), so for those the earliest
    stored bar serves as the listing lower bound — the 5-year history
    back-fill means a code's first bar ≈ its actual listing for post-2021
    IPOs, and codes listed before the window are included by construction.

    Falls back to the latest ``stock_pool`` snapshot when the lifecycle table
    is empty (pre-first-sync), so callers can rely on this one entry point.
    """
    total = db.execute(select(func.count()).select_from(SaStockLifecycle)).scalar() or 0
    if total == 0:
        logger.warning("lifecycle table empty — get_pool_asof falls back to current snapshot")
        return current_pool(db)

    rows = db.execute(
        select(
            SaStockLifecycle.stock_code,
            SaStockLifecycle.list_date,
            SaStockLifecycle.delist_date,
        )
    ).all()

    out: list[str] = []
    unknown_list: list[str] = []
    for code, list_date, delist_date in rows:
        if delist_date is not None and delist_date <= asof:
            continue  # already delisted by `asof`
        if list_date is not None:
            if list_date <= asof:
                out.append(code)
        else:
            unknown_list.append(code)

    if unknown_list:
        # One indexed group-by (uk_code_date) → earliest bar per code.
        earliest = dict(
            db.execute(
                select(DailyPrice.stock_code, func.min(DailyPrice.trade_date))
                .where(DailyPrice.stock_code.in_(unknown_list))
                .group_by(DailyPrice.stock_code)
            ).all()
        )
        for code in unknown_list:
            first = earliest.get(code)
            if first is not None and first <= asof:
                out.append(code)

    return sorted(out)


def current_pool(db: Session) -> list[str]:
    """Codes of the latest ``stock_pool`` snapshot (unchanged V2 semantics)."""
    latest_sp = db.execute(
        select(func.max(StockPool.trade_date)).select_from(StockPool)
    ).scalar()
    if latest_sp is None:
        return []
    return list(
        db.execute(
            select(StockPool.stock_code).where(StockPool.trade_date == latest_sp)
        ).scalars()
    )


def get_pool(
    db: Session, asof: date, pool: str = "current"
) -> list[str]:
    """Dispatch on the sample-governance parameter (PRD §6.3).

    ``pool="current"`` → latest snapshot (default, zero behaviour change);
    ``pool="pit"`` → :func:`get_pool_asof`.
    """
    if pool == "pit":
        return get_pool_asof(db, asof)
    return current_pool(db)


# ---------------------------------------------------------------------------
# Industry mapping reads (V2.1 BP-V2.1-006)
# ---------------------------------------------------------------------------


def get_industry(db: Session, codes: list[str], asof: date | None = None) -> dict[str, str]:
    """``{code: industry_name}`` for ``codes`` from ``sa_industry_map``.

    Prefers rows with a real board code (``BK…``) over the legacy name-space
    fallback (``em:<name>``), latest effective date ≤ ``asof`` wins.

    Fallback (V2.2): the table's history starts at the first sync (2026-08-31
    seed) — for ``asof`` dates before that there are no PIT rows at all.
    Industry membership is slow-moving, so in that case the EARLIEST snapshot
    is used instead. Strict-PIT purists should wait for history to
    accumulate; until then this keeps neutralization usable.
    """
    if not codes:
        return {}
    from app.models.kline import SaIndustryMap

    stmt = (
        select(
            SaIndustryMap.stock_code,
            SaIndustryMap.industry_code,
            SaIndustryMap.industry_name,
            SaIndustryMap.effective_date,
        )
        .where(
            SaIndustryMap.stock_code.in_(codes),
            SaIndustryMap.effective_date <= (asof or date.max),
        )
        .order_by(SaIndustryMap.effective_date.asc())
    )
    best: dict[str, str] = {}
    for code, ind_code, ind_name, _eff in db.execute(stmt).all():
        prefer = not ind_code.startswith("em:")
        if code not in best or prefer:
            best[code] = ind_name
    if best:
        return best

    # No PIT rows at all for this asof → fall back to the earliest snapshot.
    fallback = (
        select(
            SaIndustryMap.stock_code,
            SaIndustryMap.industry_code,
            SaIndustryMap.industry_name,
        )
        .where(SaIndustryMap.stock_code.in_(codes))
        .order_by(SaIndustryMap.effective_date.asc())
    )
    for code, ind_code, ind_name in db.execute(fallback).all():
        prefer = not ind_code.startswith("em:")
        if code not in best or prefer:
            best[code] = ind_name
    return best


def get_circ_mv_series(
    db: Session, codes: list[str], trade_date: date
) -> dict[str, float | None]:
    """Free-float market cap per code on ``trade_date`` (neutralization input).

    ``circ_mv = amount / (turnover/100)`` where both exist; falls back to
    ``close × 1e8``-style guesses nowhere — missing inputs yield ``None``
    (approximation via share counts is a V2.2 refinement once BP-V2.1-008
    backfills amount). Reads the ACTIVE K-line store.
    """
    from app.services.market_service import _kline_model

    model = _kline_model()
    rows = db.execute(
        select(
            model.stock_code, model.amount, model.turnover, model.close
        ).where(
            model.trade_date == trade_date,
            model.stock_code.in_(codes),
        )
    ).all()
    out: dict[str, float | None] = {}
    for code, amount, turnover, close in rows:
        if amount is not None and turnover not in (None, 0):
            out[code] = float(amount) / (float(turnover) / 100.0)
        else:
            out[code] = None
    for code in codes:
        out.setdefault(code, None)
    return out
