"""Tests for app.data.sync_industry against the LIVE DB.

SAFETY: akshare mocked; writes under sentinel code; scrubbed in finally.
"""

import pandas as pd
import pytest
from sqlalchemy import delete, select

from app.data import sync_industry
from app.models.market_data import SaStockIndustry

SENTINEL = "ZZIND"


@pytest.fixture
def cleanup(db_session):
    db_session.execute(delete(SaStockIndustry).where(SaStockIndustry.stock_code == SENTINEL))
    db_session.commit()
    try:
        yield db_session
    finally:
        db_session.execute(delete(SaStockIndustry).where(SaStockIndustry.stock_code == SENTINEL))
        db_session.commit()


def test_upsert_rows(cleanup):
    db = cleanup
    rows = [{"stock_code": SENTINEL, "industry": "白酒"}]
    assert sync_industry.upsert_rows(db, rows) == 1
    r = db.execute(select(SaStockIndustry).where(SaStockIndustry.stock_code == SENTINEL)).scalar_one()
    assert r.industry == "白酒"


def test_upsert_idempotent_and_updates(cleanup):
    db = cleanup
    sync_industry.upsert_rows(db, [{"stock_code": SENTINEL, "industry": "白酒"}])
    sync_industry.upsert_rows(db, [{"stock_code": SENTINEL, "industry": "食品饮料"}])
    db.expire_all()
    r = db.execute(select(SaStockIndustry).where(SaStockIndustry.stock_code == SENTINEL)).scalar_one()
    assert r.industry == "食品饮料"


def test_sync_one_stock(monkeypatch, cleanup):
    db = cleanup
    monkeypatch.setattr(
        sync_industry, "fetch_industry",
        lambda symbol: {"stock_code": symbol, "industry": "半导体"},
    )
    assert sync_industry.sync_one_stock(db, SENTINEL) == 1
    r = db.execute(select(SaStockIndustry).where(SaStockIndustry.stock_code == SENTINEL)).scalar_one()
    assert r.industry == "半导体"


def test_sync_one_stock_missing_returns_zero(monkeypatch, cleanup):
    monkeypatch.setattr(sync_industry, "fetch_industry", lambda symbol: None)
    assert sync_industry.sync_one_stock(cleanup, SENTINEL) == 0


def test_fetch_industry_picks_industry_row(monkeypatch):
    """fetch_industry parses the item/value frame for the 行业 row."""
    df = pd.DataFrame({"item": ["股票简称", "行业", "上市时间"], "value": ["贵州茅台", "白酒", "2001-08-27"]})
    monkeypatch.setattr(sync_industry.akshare_client.ak, "stock_individual_info_em", lambda symbol: df)
    row = sync_industry.fetch_industry("600519")
    assert row == {"stock_code": "600519", "industry": "白酒"}


def test_fetch_industry_missing_row(monkeypatch):
    df = pd.DataFrame({"item": ["股票简称"], "value": ["X"]})
    monkeypatch.setattr(sync_industry.akshare_client.ak, "stock_individual_info_em", lambda symbol: df)
    row = sync_industry.fetch_industry("600519")
    assert row is not None
    assert row["industry"] is None
