"""Chip-distribution (筹码峰) read service (V1.5 BP-V1.5-007).

Zero ingestion: reads the pre-computed ``chip_distribution`` table (populated
by an external CYQ pipeline, ≈470k rows) and returns the scalar rollups +
the distribution histogram for the chip-peak display.

The ``distribution`` column is a JSON text array of ``[price, weight]`` pairs;
we parse it lazily so a malformed entry degrades to ``None`` rather than 500ing.
"""

import json
import logging
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.stock import ChipDistribution

logger = logging.getLogger(__name__)


def get_chip(
    db: Session, code: str, trade_date: Optional[date] = None
) -> dict | None:
    """Return the chip-distribution snapshot for ``code``.

    :param trade_date: when None, the latest available snapshot is returned.
    :return: dict with ``trade_date, profit_ratio, avg_cost, concentration_90,
        concentration_70, cost_90_low, cost_90_high, distribution`` (the last
        is a parsed list of ``[price, weight]``), or None if no row exists.
    """
    stmt = select(ChipDistribution).where(ChipDistribution.stock_code == code)
    if trade_date is not None:
        stmt = stmt.where(ChipDistribution.trade_date == trade_date)
        stmt = stmt.limit(1)
    else:
        stmt = stmt.order_by(ChipDistribution.trade_date.desc()).limit(1)
    row = db.execute(stmt).scalar_one_or_none()
    if row is None:
        return None

    dist = None
    if row.distribution:
        try:
            dist = json.loads(row.distribution)
        except (TypeError, ValueError):
            logger.warning("chip: unparseable distribution for %s @ %s", code, row.trade_date)
            dist = None

    return {
        "trade_date": row.trade_date,
        "profit_ratio": float(row.profit_ratio) if row.profit_ratio is not None else None,
        "avg_cost": float(row.avg_cost) if row.avg_cost is not None else None,
        "concentration_90": float(row.concentration_90) if row.concentration_90 is not None else None,
        "concentration_70": float(row.concentration_70) if row.concentration_70 is not None else None,
        "cost_90_low": float(row.cost_90_low) if row.cost_90_low is not None else None,
        "cost_90_high": float(row.cost_90_high) if row.cost_90_high is not None else None,
        "distribution": dist,
    }
