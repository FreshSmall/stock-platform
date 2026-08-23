"""Tests for app.data.sync_pool against the LIVE ``stock_analysis`` DB.

SAFETY (same conventions as test_sync_daily): the network fetch is mocked and
every test writes only under the sentinel ``pool_name='ZZPOOL'`` / sentinel
stock codes, scrubbed in ``finally``. The real 'default' pool snapshots are
never touched — the industry carry-over is verified by first inserting a
sentinel PRIOR snapshot.
"""

from datetime import date

import pytest
from sqlalchemy import delete, select

from app.data import sync_pool
from app.models.stock import StockPool

TEST_POOL = "ZZPOOL"
# Sentinel codes: 9xxxxx would map to 'sh' but are not real listed codes
# here; they can never collide with the real universe ('default' pool uses
# real codes, and we filter by pool_name anyway).
PRIOR_DATE = date(2026, 8, 12)
# NEW_DATE must stay BELOW the newest REAL snapshot's date: _prior_industries
# takes the max snapshot below NEW_DATE, and a real 'default' snapshot dated
# 2026-08-14 exists in the shared DB (written by the 2026-08-17 universe
# refresh) which lacks the sentinel codes.
NEW_DATE = date(2026, 8, 13)


def _spots():
    return [
        {
            "stock_code": "900001",
            "stock_name": "POOL_TEST_ONE",
            "close": 10.5,
            "pct_change": 1.25,
            "turnover": 2.5,
            "pe": 15.0,
            "pb": 2.0,
            "total_mv": 105.0,   # 亿元 — stock_pool's convention
            "circ_mv": 80.0,
        },
        {
            "stock_code": "900002",
            "stock_name": "POOL_TEST_TWO",  # listed after the prior snapshot
            "close": None,  # suspended → NaN in the wild
            "pct_change": None,
            "turnover": None,
            "pe": None,
            "pb": None,
            "total_mv": None,
            "circ_mv": None,
        },
    ]


@pytest.fixture
def cleanup_sentinel(db_session):
    db = db_session
    db.execute(delete(StockPool).where(StockPool.pool_name == TEST_POOL))
    db.commit()
    try:
        yield db
    finally:
        db.execute(delete(StockPool).where(StockPool.pool_name == TEST_POOL))
        db.commit()


def test_sync_writes_snapshot_and_carries_industry(cleanup_sentinel, monkeypatch):
    """A fresh snapshot inherits industry per code from the prior snapshot;
    codes unknown to it stay NULL (industry isn't in the spot table)."""
    db = cleanup_sentinel
    db.add(
        StockPool(
            pool_name=TEST_POOL, trade_date=PRIOR_DATE, stock_code="900001",
            stock_name="OLD", industry="半导体",
        )
    )
    db.commit()
    monkeypatch.setattr(
        "app.data.akshare_client.fetch_spot_table", _spots
    )

    n = sync_pool.sync_pool_snapshot(db, pool_name=TEST_POOL, trade_date=NEW_DATE)
    assert n == 2

    rows = db.execute(
        select(StockPool)
        .where(StockPool.pool_name == TEST_POOL, StockPool.trade_date == NEW_DATE)
        .order_by(StockPool.stock_code)
    ).scalars().all()
    one, two = rows
    assert one.industry == "半导体"      # carried over from the prior snapshot
    assert two.industry is None          # new listing, no history
    assert one.close == 10.5 and one.pct_change == 1.25
    assert one.exchange == "sh"          # 9xxxxx → sh
    assert two.exchange == "sh"
    assert two.close is None             # suspended day survives as a row


def test_sync_replaces_same_day_snapshot(cleanup_sentinel, monkeypatch):
    """Re-running on the same day replaces the snapshot — no duplicates."""
    db = cleanup_sentinel
    monkeypatch.setattr(
        "app.data.akshare_client.fetch_spot_table", _spots
    )
    assert sync_pool.sync_pool_snapshot(db, pool_name=TEST_POOL, trade_date=NEW_DATE) == 2

    trimmed = [_spots()[0]]  # next fetch "lost" one code
    monkeypatch.setattr(
        "app.data.akshare_client.fetch_spot_table", lambda: trimmed
    )
    assert sync_pool.sync_pool_snapshot(db, pool_name=TEST_POOL, trade_date=NEW_DATE) == 1

    rows = db.execute(
        select(StockPool)
        .where(StockPool.pool_name == TEST_POOL, StockPool.trade_date == NEW_DATE)
    ).scalars().all()
    assert [r.stock_code for r in rows] == ["900001"]


def test_sync_empty_fetch_writes_nothing(cleanup_sentinel, monkeypatch):
    """A failed/empty upstream fetch must not wipe the previous snapshot."""
    db = cleanup_sentinel
    db.add(
        StockPool(
            pool_name=TEST_POOL, trade_date=PRIOR_DATE, stock_code="900001",
            stock_name="OLD",
        )
    )
    db.commit()
    monkeypatch.setattr(
        "app.data.akshare_client.fetch_spot_table", lambda: []
    )
    assert sync_pool.sync_pool_snapshot(db, pool_name=TEST_POOL, trade_date=NEW_DATE) == 0
    kept = db.execute(
        select(StockPool).where(StockPool.pool_name == TEST_POOL)
    ).scalars().all()
    assert [r.trade_date for r in kept] == [PRIOR_DATE]
