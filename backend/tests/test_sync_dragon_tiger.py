"""Tests for app.data.sync_dragon_tiger against the LIVE DB.

SAFETY: akshare mocked; writes under sentinel codes only; scrubbed in finally.
"""

import pytest
from sqlalchemy import delete, select

from app.data import sync_dragon_tiger
from app.models.market_data import SaDragonTiger, SaDragonTigerSeat

SENTINEL = "ZZDTB"
DATE = "2026-07-28"


@pytest.fixture
def cleanup(db_session):
    for tbl_model in (SaDragonTigerSeat, SaDragonTiger):
        db_session.execute(
            delete(tbl_model).where(tbl_model.stock_code == SENTINEL)
        )
    db_session.commit()
    try:
        yield db_session
    finally:
        for tbl_model in (SaDragonTigerSeat, SaDragonTiger):
            db_session.execute(
                delete(tbl_model).where(tbl_model.stock_code == SENTINEL)
            )
        db_session.commit()


def test_upsert_stocks(cleanup):
    db = cleanup
    rows = [{
        "stock_code": SENTINEL, "stock_name": "TEST", "trade_date": DATE,
        "reason": "日涨幅偏离值", "net_buy": 1000.0, "buy_amount": 5000.0, "sell_amount": 4000.0,
    }]
    assert sync_dragon_tiger.upsert_stocks(db, rows) == 1
    r = db.execute(select(SaDragonTiger).where(SaDragonTiger.stock_code == SENTINEL)).scalar_one()
    assert r.net_buy == 1000.0


def test_upsert_seats(cleanup):
    db = cleanup
    seats = {
        "buy": [
            {"seat_name": "机构专用", "buy_amount": 100.0, "sell_amount": 0.0, "net_amount": 100.0, "is_institution": 1},
            {"seat_name": "某营业部", "buy_amount": 50.0, "sell_amount": 10.0, "net_amount": 40.0, "is_institution": 0},
        ],
        "sell": [{"seat_name": "另一营业部", "buy_amount": 5.0, "sell_amount": 80.0, "net_amount": -75.0, "is_institution": 0}],
    }
    n = sync_dragon_tiger.upsert_seats(db, DATE, SENTINEL, seats)
    assert n == 3
    # rank assignment: buy side ranks 1,2; sell side rank 1
    buy = db.execute(
        select(SaDragonTigerSeat).where(SaDragonTigerSeat.stock_code == SENTINEL, SaDragonTigerSeat.side == 1)
    ).scalars().all()
    assert sorted(s.rank for s in buy) == [1, 2]
    assert buy[0].is_institution == 1  # 机构专用 flagged


def test_upsert_seats_empty(cleanup):
    assert sync_dragon_tiger.upsert_seats(cleanup, DATE, SENTINEL, {"buy": [], "sell": []}) == 0


def test_sync_date_full(monkeypatch, cleanup):
    db = cleanup
    monkeypatch.setattr(
        sync_dragon_tiger.akshare_client, "fetch_dragon_tiger",
        lambda d: [{"stock_code": SENTINEL, "stock_name": "T", "trade_date": DATE, "reason": "x", "net_buy": 1.0, "buy_amount": 2.0, "sell_amount": 1.0}],
    )
    monkeypatch.setattr(
        sync_dragon_tiger.akshare_client, "fetch_dragon_tiger_seats",
        lambda code, d: {"buy": [{"seat_name": "S", "buy_amount": 1.0, "sell_amount": 0.0, "net_amount": 1.0, "is_institution": 0}], "sell": []},
    )
    n = sync_dragon_tiger.sync_date(db, DATE, fetch_seats=True)
    assert n == 1
    seats = db.execute(select(SaDragonTigerSeat).where(SaDragonTigerSeat.stock_code == SENTINEL)).scalars().all()
    assert len(seats) == 1


def test_sync_date_seats_failure_doesnt_abort(monkeypatch, cleanup):
    """A seat fetch error on one stock is logged but doesn't abort the stock list."""
    db = cleanup
    monkeypatch.setattr(
        sync_dragon_tiger.akshare_client, "fetch_dragon_tiger",
        lambda d: [{"stock_code": SENTINEL, "trade_date": DATE}],
    )

    def boom(code, d):
        raise RuntimeError("network down")

    monkeypatch.setattr(sync_dragon_tiger.akshare_client, "fetch_dragon_tiger_seats", boom)
    n = sync_dragon_tiger.sync_date(db, DATE, fetch_seats=True)
    assert n == 1  # stock list still written
