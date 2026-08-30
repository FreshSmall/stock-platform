"""Tests for the finance-extra sync and the IC trade-date fallback."""

from datetime import date
from unittest.mock import patch

import pandas as pd
from sqlalchemy import delete, select

from app.data import akshare_client as ac
from app.data import sync_finance
from app.models.finance import SaFinancialExtra
from app.services import factor_service

_SENTINELS = ["999901", "999902"]


# --- akshare frame → canonical rows ------------------------------------------


def test_fetch_financial_abstract_maps_indicators():
    frame = pd.DataFrame(
        {
            "选项": ["常用指标", "常用指标", "成长能力", "成长能力"],
            "指标": [
                "基本每股收益", "净资产收益率(ROE)",
                "营业总收入增长率", "归属母公司净利润增长率",
            ],
            "20260630": [36.82, 17.72, 1.4699, -2.029],
            "20260331": [22.48, 10.06, 6.538, 1.3653],
        }
    )
    with (
        patch.object(ac.ak, "stock_financial_abstract", return_value=frame),
        patch.object(ac, "_throttle", lambda: None),
    ):
        rows = ac.fetch_financial_abstract("600519")
    assert [r["report_date"] for r in rows] == ["2026-03-31", "2026-06-30"]
    last = rows[-1]
    assert last["eps"] == 36.82 and last["roe"] == 17.72
    assert last["revenue_growth"] == 1.4699 and last["profit_growth"] == -2.029


def test_fetch_financial_abstract_skips_all_empty_periods():
    frame = pd.DataFrame(
        {
            "选项": ["常用指标", "常用指标", "成长能力", "成长能力"],
            "指标": [
                "基本每股收益", "净资产收益率(ROE)",
                "营业总收入增长率", "归属母公司净利润增长率",
            ],
            "20260630": [None, None, None, None],
            "20260331": [1.0, 2.0, 3.0, 4.0],
        }
    )
    with (
        patch.object(ac.ak, "stock_financial_abstract", return_value=frame),
        patch.object(ac, "_throttle", lambda: None),
    ):
        rows = ac.fetch_financial_abstract("600519")
    assert len(rows) == 1 and rows[0]["report_date"] == "2026-03-31"


# --- upsert idempotence -------------------------------------------------------


def test_sync_one_stock_upserts_idempotently(db_session):
    fake = [
        {
            "stock_code": "999901", "report_date": "2026-06-30",
            "roe": 17.7, "eps": 36.8, "revenue_growth": 1.5, "profit_growth": -2.0,
        },
        {
            "stock_code": "999901", "report_date": "2026-03-31",
            "roe": 10.1, "eps": 22.5, "revenue_growth": 6.5, "profit_growth": 1.4,
        },
    ]
    try:
        with patch.object(sync_finance.akshare_client, "fetch_financial_abstract", return_value=fake):
            assert sync_finance.sync_one_stock(db_session, "999901") == 2
            assert sync_finance.sync_one_stock(db_session, "999901") == 2  # idempotent
        rows = db_session.execute(
            select(SaFinancialExtra).where(SaFinancialExtra.stock_code == "999901")
        ).scalars().all()
        assert len(rows) == 2
        latest = max(rows, key=lambda r: r.report_date)
        assert float(latest.roe) == 17.7
    finally:
        db_session.execute(
            delete(SaFinancialExtra).where(SaFinancialExtra.stock_code.in_(_SENTINELS))
        )
        db_session.commit()


# --- IC trade-date fallback ----------------------------------------------------


def test_compute_ic_falls_back_when_trade_date_lacks_forward_days(db_session):
    """trade_date=today has no forward bars → effective date moves back."""
    result = factor_service.compute_ic(db_session, "ma5", date.today(), horizon=5)
    assert result is not None, "fallback should produce data, not None"
    assert result["trade_date"] < date.today().isoformat()
    assert result["universe_size"] >= 10


def test_compute_ic_keeps_valid_past_trade_date(db_session):
    """A trading day with enough forward days is used as-is (no fallback)."""
    result = factor_service.compute_ic(db_session, "ma5", date(2026, 8, 13), horizon=5)
    assert result is not None
    assert result["trade_date"] == "2026-08-13"
