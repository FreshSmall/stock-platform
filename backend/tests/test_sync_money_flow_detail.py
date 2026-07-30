"""Tests for app.data.sync_money_flow_detail against the LIVE DB.

SAFETY: akshare mocked; writes under sentinel code; scrubbed in finally.
"""

import pytest
from sqlalchemy import delete, select

from app.data import sync_money_flow_detail as smfd
from app.models.market_data import SaMoneyFlowDetail

SENTINEL = "ZZMFD"
DATE = "2026-07-28"


@pytest.fixture
def cleanup(db_session):
    db_session.execute(delete(SaMoneyFlowDetail).where(SaMoneyFlowDetail.stock_code == SENTINEL))
    db_session.commit()
    try:
        yield db_session
    finally:
        db_session.execute(delete(SaMoneyFlowDetail).where(SaMoneyFlowDetail.stock_code == SENTINEL))
        db_session.commit()


def _row(date=DATE):
    return {
        "stock_code": SENTINEL, "trade_date": date,
        "super_net": 100.0, "big_net": 50.0, "medium_net": -30.0, "small_net": -120.0,
    }


def test_upsert_rows(cleanup):
    db = cleanup
    assert smfd.upsert_rows(db, [_row()]) == 1
    r = db.execute(select(SaMoneyFlowDetail).where(SaMoneyFlowDetail.stock_code == SENTINEL)).scalar_one()
    assert float(r.super_net) == 100.0
    assert float(r.small_net) == -120.0


def test_upsert_idempotent(cleanup):
    db = cleanup
    smfd.upsert_rows(db, [_row()])
    smfd.upsert_rows(db, [_row()])  # update
    db.expire_all()
    cnt = db.execute(select(SaMoneyFlowDetail).where(SaMoneyFlowDetail.stock_code == SENTINEL)).scalars().all()
    assert len(cnt) == 1


def test_market_inference():
    assert smfd._market_of("600519") == "sh"
    assert smfd._market_of("000001") == "sz"
    assert smfd._market_of("300750") == "sz"
    assert smfd._market_of("XYZ") is None
    assert smfd._market_of("") is None


def test_sync_one_stock_infers_market(monkeypatch, cleanup):
    db = cleanup
    called = {}

    def fake(code, market):
        called["market"] = market
        return [_row()]

    monkeypatch.setattr(smfd.akshare_client, "fetch_money_flow_detail", fake)
    # 600xxx → sh inferred
    assert smfd.sync_one_stock(db, SENTINEL, market="sh") == 1
    assert called["market"] == "sh"


def test_sync_one_stock_unknown_market(monkeypatch, cleanup):
    """A code that can't resolve to a market writes nothing (logs warning)."""
    monkeypatch.setattr(smfd.akshare_client, "fetch_money_flow_detail", lambda *a, **k: [])
    assert smfd.sync_one_stock(cleanup, "XYZ123") == 0
