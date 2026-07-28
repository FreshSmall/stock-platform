"""Supplementary finance & money-flow sync into ``sa_money_flow`` / ``sa_financial_extra``.

NOTE: AkShare APIs for these (``stock_individual_fund_flow``,
``stock_financial_abstract``) return dataframes with varying schemas across
versions. This module provides the idempotent UPSERT plumbing; the exact
akshare call + column mapping is left to B-later (see TODO stubs in
``akshare_client.fetch_money_flow`` / ``fetch_financial_abstract``). We do NOT
guess column names here.
"""

import logging

from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.data import akshare_client
from app.models.finance import SaFinancialExtra, SaMoneyFlow

logger = logging.getLogger(__name__)


def upsert_money_flow(db: Session, rows: list[dict]) -> int:
    """UPSERT money-flow rows into ``sa_money_flow``.

    Keyed on ``uk_code_date(stock_code, trade_date)``.

    :param rows: list of ``{stock_code, trade_date, main_net_inflow}``.
    :return: rows written (0 if ``rows`` is empty).
    """
    if not rows:
        return 0
    payload = [
        {
            "stock_code": r["stock_code"],
            "trade_date": r["trade_date"],
            "main_net_inflow": r.get("main_net_inflow"),
        }
        for r in rows
    ]
    stmt = mysql_insert(SaMoneyFlow).values(payload)
    stmt = stmt.on_duplicate_key_update(
        {"main_net_inflow": stmt.inserted.main_net_inflow}
    )
    db.execute(stmt)
    db.commit()
    return len(payload)


def upsert_financial_extra(db: Session, rows: list[dict]) -> int:
    """UPSERT financial-extra rows into ``sa_financial_extra``.

    Keyed on ``uk_code_report(stock_code, report_date)``.

    :param rows: list of ``{stock_code, report_date, roe, eps,
        revenue_growth, profit_growth}``.
    :return: rows written (0 if ``rows`` is empty).
    """
    if not rows:
        return 0
    payload = [
        {
            "stock_code": r["stock_code"],
            "report_date": r["report_date"],
            "roe": r.get("roe"),
            "eps": r.get("eps"),
            "revenue_growth": r.get("revenue_growth"),
            "profit_growth": r.get("profit_growth"),
        }
        for r in rows
    ]
    stmt = mysql_insert(SaFinancialExtra).values(payload)
    update_cols = {
        c: getattr(stmt.inserted, c)
        for c in ("roe", "eps", "revenue_growth", "profit_growth")
    }
    stmt = stmt.on_duplicate_key_update(update_cols)
    db.execute(stmt)
    db.commit()
    return len(payload)
