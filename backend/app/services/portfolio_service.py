"""Portfolio service (BP-V2-005): CRUD + weighted net-value + backtest.

A portfolio's performance = the weighted average of its constituents'
buy-and-hold returns. This is simpler (and more meaningful for "how did this
basket do") than running a trading strategy over each stock; a strategy-based
portfolio backtest can reuse :mod:`backtest_service` per-stock if needed.

Functions take a Session and return plain dicts ready for the API.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.portfolio import SaPortfolio, SaPortfolioHolding
from app.models.stock import DailyPrice

logger = logging.getLogger(__name__)


def _to_float(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def list_portfolios(db: Session, user_id: int | None = None) -> list[dict]:
    stmt = select(SaPortfolio)
    if user_id is not None:
        stmt = stmt.where(SaPortfolio.user_id == user_id)
    rows = db.execute(stmt.order_by(SaPortfolio.id.desc())).scalars().all()
    out = []
    for p in rows:
        holdings = _holdings(db, p.id)
        out.append(
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "benchmark": p.benchmark,
                "holdings": holdings,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
        )
    return out


def _holdings(db: Session, portfolio_id: int) -> list[dict]:
    rows = db.execute(
        select(SaPortfolioHolding)
        .where(SaPortfolioHolding.portfolio_id == portfolio_id)
        .order_by(SaPortfolioHolding.stock_code)
    ).scalars().all()
    return [
        {"stock_code": h.stock_code, "weight": _to_float(h.weight)}
        for h in rows
    ]


def get_portfolio(db: Session, portfolio_id: int) -> dict | None:
    p = db.get(SaPortfolio, portfolio_id)
    if p is None:
        return None
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "benchmark": p.benchmark,
        "holdings": _holdings(db, p.id),
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def create_portfolio(
    db: Session,
    name: str,
    holdings: list[dict],
    user_id: int | None = None,
    description: str | None = None,
    benchmark: str = "sh000001",
) -> dict:
    """Create a portfolio + its holdings. Weights are normalised to sum=1."""
    p = SaPortfolio(
        user_id=user_id, name=name, description=description, benchmark=benchmark
    )
    db.add(p)
    db.flush()  # get p.id
    total_w = sum(float(h.get("weight", 1.0)) for h in holdings) or 1.0
    for h in holdings:
        db.add(
            SaPortfolioHolding(
                portfolio_id=p.id,
                stock_code=h["stock_code"],
                weight=Decimal(str(round(float(h.get("weight", 1.0)) / total_w, 4))),
            )
        )
    db.commit()
    db.refresh(p)
    return get_portfolio(db, p.id)


def update_portfolio(
    db: Session,
    portfolio_id: int,
    name: str | None = None,
    description: str | None = None,
    benchmark: str | None = None,
    holdings: list[dict] | None = None,
) -> dict | None:
    p = db.get(SaPortfolio, portfolio_id)
    if p is None:
        return None
    if name is not None:
        p.name = name
    if description is not None:
        p.description = description
    if benchmark is not None:
        p.benchmark = benchmark
    if holdings is not None:
        db.execute(
            delete(SaPortfolioHolding).where(
                SaPortfolioHolding.portfolio_id == portfolio_id
            )
        )
        total_w = sum(float(h.get("weight", 1.0)) for h in holdings) or 1.0
        for h in holdings:
            db.add(
                SaPortfolioHolding(
                    portfolio_id=portfolio_id,
                    stock_code=h["stock_code"],
                    weight=Decimal(str(round(float(h.get("weight", 1.0)) / total_w, 4))),
                )
            )
    db.commit()
    return get_portfolio(db, portfolio_id)


def delete_portfolio(db: Session, portfolio_id: int) -> bool:
    p = db.get(SaPortfolio, portfolio_id)
    if p is None:
        return False
    db.execute(
        delete(SaPortfolioHolding).where(
            SaPortfolioHolding.portfolio_id == portfolio_id
        )
    )
    db.delete(p)
    db.commit()
    return True


def portfolio_nav(
    db: Session,
    portfolio_id: int,
    start: date | None = None,
    end: date | None = None,
) -> dict | None:
    """Weighted buy-and-hold net value of the portfolio over [start, end].

    Returns ``{nav_curve, benchmark_curve, return_rate, benchmark_return,
    max_drawdown}``. NAV is normalised to 1.0 at the start.
    """
    p = db.get(SaPortfolio, portfolio_id)
    if p is None:
        return None
    holdings = _holdings(db, portfolio_id)
    if not holdings:
        return {"nav_curve": [], "benchmark_curve": [], "return_rate": None}

    # gather close pivots: date × stock
    codes = [h["stock_code"] for h in holdings]
    weights = {h["stock_code"]: (h["weight"] or 0) for h in holdings}
    end = end or date.today()
    start = start or (end - timedelta(days=365))
    rows = db.execute(
        select(DailyPrice.trade_date, DailyPrice.stock_code, DailyPrice.close)
        .where(
            DailyPrice.stock_code.in_(codes),
            DailyPrice.trade_date >= start,
            DailyPrice.trade_date <= end,
            DailyPrice.close.is_not(None),
        )
        .order_by(DailyPrice.trade_date.asc())
    ).all()
    if not rows:
        return {"nav_curve": [], "benchmark_curve": [], "return_rate": None}

    df = pd.DataFrame(rows, columns=["date", "code", "close"])
    df["close"] = df["close"].astype(float)
    pivot = df.pivot(index="date", columns="code", values="close").sort_index()
    # daily returns per stock
    rets = pivot.pct_change().fillna(0)
    # weighted portfolio daily return
    w = pd.Series({c: weights.get(c, 0) for c in pivot.columns})
    w = w / w.sum()  # renormalise
    port_ret = (rets[w.index] * w).sum(axis=1)
    nav = (1 + port_ret).cumprod()

    nav_curve = [
        {"date": d.isoformat(), "nav": round(float(v), 6)}
        for d, v in nav.items()
    ]
    # max drawdown
    peak = nav.cummax()
    dd = (nav - peak) / peak
    max_dd = round(float(dd.min()) * 100, 4) if not dd.empty else None

    return {
        "nav_curve": nav_curve,
        "return_rate": round(float(nav.iloc[-1] - 1) * 100, 4),
        "max_drawdown": max_dd,
        "holdings": holdings,
    }
