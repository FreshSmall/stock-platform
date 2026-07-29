"""Tests for app.data.sync_north_flow against the LIVE DB.

SAFETY: akshare mocked; writes under sentinel date/channel; scrubbed in finally.
"""

import pytest
from sqlalchemy import delete, select

from app.data import sync_north_flow
from app.models.market_data import SaNorthFlow

DATE = "2026-07-28"


@pytest.fixture
def cleanup(db_session):
    db_session.execute(delete(SaNorthFlow).where(SaNorthFlow.trade_date == DATE))
    db_session.commit()
    try:
        yield db_session
    finally:
        db_session.execute(delete(SaNorthFlow).where(SaNorthFlow.trade_date == DATE))
        db_session.commit()


def test_upsert_rows(cleanup):
    db = cleanup
    rows = [
        {"trade_date": DATE, "channel": "sh", "net_buy": 100.0, "buy_amount": 200.0, "sell_amount": 100.0},
        {"trade_date": DATE, "channel": "sz", "net_buy": -50.0, "buy_amount": 80.0, "sell_amount": 130.0},
    ]
    assert sync_north_flow.upsert_rows(db, rows) == 2
    fetched = db.execute(select(SaNorthFlow).where(SaNorthFlow.trade_date == DATE)).scalars().all()
    assert {f.channel for f in fetched} == {"sh", "sz"}


def test_upsert_is_idempotent(cleanup):
    db = cleanup
    rows = [{"trade_date": DATE, "channel": "sh", "net_buy": 100.0}]
    sync_north_flow.upsert_rows(db, rows)
    rows[0]["net_buy"] = 999.0
    sync_north_flow.upsert_rows(db, rows)
    db.expire_all()
    r = db.execute(
        select(SaNorthFlow).where(SaNorthFlow.trade_date == DATE, SaNorthFlow.channel == "sh")
    ).scalar_one()
    assert float(r.net_buy) == 999.0


def test_drops_invalid_rows(cleanup):
    rows = [
        {"trade_date": DATE, "channel": "sh", "net_buy": 1.0},
        {"trade_date": None, "channel": "sh"},  # missing date
        {"trade_date": DATE, "channel": None},  # missing channel
    ]
    assert sync_north_flow.upsert_rows(cleanup, rows) == 1


def test_sync_all_uses_client(monkeypatch, cleanup):
    db = cleanup
    monkeypatch.setattr(
        sync_north_flow.akshare_client, "fetch_north_flow",
        lambda: [{"trade_date": DATE, "channel": "sh", "net_buy": 1.0}],
    )
    assert sync_north_flow.sync_all(db) == 1
