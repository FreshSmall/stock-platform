"""Read-side query services for V1.5 market data (BP-V1.5-002/003/004).

Thin data-access layer over the V1.5 ``sa_`` tables populated by the sync
modules: money-flow detail (per stock), northbound flow (market-wide), and
dragon-tiger board (stocks + seats). Each function takes a Session and returns
plain dicts ready for the API serializer.
"""

from datetime import date
from typing import Optional

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.market_data import (
    SaDragonTiger,
    SaDragonTigerSeat,
    SaMoneyFlowDetail,
    SaNorthFlow,
)


def get_money_flow_detail(db: Session, code: str, days: int = 30) -> list[dict]:
    """Four-tier money-flow detail for a stock, most-recent ``days`` rows.

    :return: list of dicts (trade_date, super/big/medium/small_net).
    """
    rows = db.execute(
        select(SaMoneyFlowDetail)
        .where(SaMoneyFlowDetail.stock_code == code)
        .order_by(desc(SaMoneyFlowDetail.trade_date))
        .limit(days)
    ).scalars().all()
    return [
        {
            "trade_date": r.trade_date,
            "super_net": float(r.super_net) if r.super_net is not None else None,
            "big_net": float(r.big_net) if r.big_net is not None else None,
            "medium_net": float(r.medium_net) if r.medium_net is not None else None,
            "small_net": float(r.small_net) if r.small_net is not None else None,
        }
        for r in reversed(rows)  # ascending for charting
    ]


def get_north_flow(db: Session, days: int = 30) -> list[dict]:
    """Daily northbound net inflow (both channels) for the recent ``days``.

    :return: list of dicts (trade_date, sh_net, sz_net) merged by date.
    """
    rows = db.execute(
        select(SaNorthFlow)
        .order_by(desc(SaNorthFlow.trade_date))
        .limit(days * 2)  # 2 channels per day
    ).scalars().all()
    by_date: dict[date, dict] = {}
    for r in rows:
        d = by_date.setdefault(r.trade_date, {"trade_date": r.trade_date, "sh_net": None, "sz_net": None})
        net = float(r.net_buy) if r.net_buy is not None else None
        if r.channel == "sh":
            d["sh_net"] = net
        else:
            d["sz_net"] = net
    return sorted(by_date.values(), key=lambda x: x["trade_date"])


def list_dragon_tiger(db: Session, trade_date: Optional[date] = None) -> list[dict]:
    """The dragon-tiger stock list for one day (latest if trade_date is None).

    :return: list of dicts (stock_code/name/reason/net_buy/...).
    """
    stmt = select(SaDragonTiger)
    if trade_date is not None:
        stmt = stmt.where(SaDragonTiger.trade_date == trade_date)
    else:
        stmt = stmt.order_by(desc(SaDragonTiger.trade_date)).limit(1).subquery()
        # re-select for the latest date
        latest = db.execute(
            select(SaDragonTiger).order_by(desc(SaDragonTiger.trade_date)).limit(1)
        ).scalar_one_or_none()
        if latest is None:
            return []
        trade_date = latest.trade_date
        stmt = select(SaDragonTiger).where(SaDragonTiger.trade_date == trade_date)
    rows = db.execute(stmt).scalars().all()
    return [
        {
            "stock_code": r.stock_code,
            "stock_name": r.stock_name,
            "trade_date": r.trade_date,
            "reason": r.reason,
            "net_buy": float(r.net_buy) if r.net_buy is not None else None,
            "buy_amount": float(r.buy_amount) if r.buy_amount is not None else None,
            "sell_amount": float(r.sell_amount) if r.sell_amount is not None else None,
        }
        for r in rows
    ]


def get_dragon_tiger_seats(
    db: Session, code: str, trade_date: date
) -> dict:
    """Top-5 buy/sell seats for one stock/day.

    :return: ``{"buy": [...], "sell": [...]}``.
    """
    rows = db.execute(
        select(SaDragonTigerSeat).where(
            SaDragonTigerSeat.stock_code == code,
            SaDragonTigerSeat.trade_date == trade_date,
        )
    ).scalars().all()
    out: dict[str, list] = {"buy": [], "sell": []}
    for r in rows:
        seat = {
            "rank": r.rank,
            "seat_name": r.seat_name,
            "buy_amount": float(r.buy_amount) if r.buy_amount is not None else None,
            "sell_amount": float(r.sell_amount) if r.sell_amount is not None else None,
            "net_amount": float(r.net_amount) if r.net_amount is not None else None,
            "is_institution": bool(r.is_institution),
        }
        out["buy" if r.side == 1 else "sell"].append(seat)
    out["buy"].sort(key=lambda s: s["rank"])
    out["sell"].sort(key=lambda s: s["rank"])
    return out


def get_dragon_tiger_history(db: Session, code: str) -> list[dict]:
    """All dragon-tiger listings for one stock, most-recent first."""
    rows = db.execute(
        select(SaDragonTiger)
        .where(SaDragonTiger.stock_code == code)
        .order_by(desc(SaDragonTiger.trade_date))
    ).scalars().all()
    return [
        {
            "trade_date": r.trade_date,
            "reason": r.reason,
            "net_buy": float(r.net_buy) if r.net_buy is not None else None,
        }
        for r in rows
    ]
