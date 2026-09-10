"""Sample-governance filters (V2.1 BP-V2.1-004/005, PRD §6.3).

One helper the research stack (IC / scoring / backtest) shares: given codes
and a trade date, drop codes the sample flags exclude and report coverage so
callers can show how much of the universe the flags actually judged (ST
history only exists since the platform's daily snapshots).
"""

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.kline import SaDailyTradeStatus

logger = logging.getLogger(__name__)


def apply_sample_filters(
    db: Session,
    codes: list[str],
    trade_date: date,
    exclude_st: bool = False,
    exclude_suspended: bool = False,
    only_tradable: bool = False,
) -> tuple[list[str], dict]:
    """Filter ``codes`` by trade status on ``trade_date``.

    :return: ``(filtered_codes, meta)`` where meta reports per-flag exclusion
        counts and the status-table coverage (codes with a status row / all
        codes). Codes without a status row are KEPT — the patrol surfaces
        missing coverage instead of silently shrinking samples.
    """
    if not codes or not (exclude_st or exclude_suspended or only_tradable):
        return codes, {"coverage": None, "excluded": {}}

    rows = db.execute(
        select(
            SaDailyTradeStatus.stock_code,
            SaDailyTradeStatus.is_st,
            SaDailyTradeStatus.is_suspended,
            SaDailyTradeStatus.buy_tradable,
        ).where(
            SaDailyTradeStatus.trade_date == trade_date,
            SaDailyTradeStatus.stock_code.in_(codes),
        )
    ).all()
    status = {code: (is_st, suspended, buyable) for code, is_st, suspended, buyable in rows}

    excluded = {"st": 0, "suspended": 0, "untradable": 0, "no_status": 0}
    out: list[str] = []
    for code in codes:
        st = status.get(code)
        if st is None:
            excluded["no_status"] += 1
            out.append(code)  # keep: no evidence to exclude on
            continue
        is_st, suspended, buyable = st
        if exclude_st and is_st:
            excluded["st"] += 1
            continue
        if exclude_suspended and suspended:
            excluded["suspended"] += 1
            continue
        if only_tradable and not buyable:
            excluded["untradable"] += 1
            continue
        out.append(code)
    meta = {
        "coverage": round(len(status) / len(codes), 4) if codes else None,
        "excluded": excluded,
    }
    return out, meta
