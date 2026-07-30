"""Tests for app.data.sync_index against the LIVE DB.

SAFETY: akshare mocked; writes under sentinel index code; scrubbed in finally.
"""

import pytest
from sqlalchemy import delete, select

from app.data import sync_index
from app.models.market_data import SaIndexQuote

SENTINEL = "shZZTST"


@pytest.fixture
def cleanup(db_session):
    db_session.execute(delete(SaIndexQuote).where(SaIndexQuote.index_code == SENTINEL))
    db_session.commit()
    try:
        yield db_session
    finally:
        db_session.execute(delete(SaIndexQuote).where(SaIndexQuote.index_code == SENTINEL))
        db_session.commit()


def test_upsert_rows(cleanup):
    db = cleanup
    rows = [
        {"index_code": SENTINEL, "index_name": "测试", "trade_date": "2026-07-28",
         "close": 3000.0, "pct_change": 1.0, "open": 2990.0, "high": 3010.0, "low": 2980.0, "amount": 100.0},
        {"index_code": SENTINEL, "index_name": "测试", "trade_date": "2026-07-29",
         "close": 3030.0, "pct_change": 1.0, "open": 3000.0, "high": 3040.0, "low": 2995.0, "amount": 110.0},
    ]
    assert sync_index.upsert_rows(db, rows) == 2
    fetched = db.execute(select(SaIndexQuote).where(SaIndexQuote.index_code == SENTINEL)).scalars().all()
    assert len(fetched) == 2


def test_upsert_idempotent(cleanup):
    db = cleanup
    rows = [{"index_code": SENTINEL, "trade_date": "2026-07-28", "close": 3000.0}]
    sync_index.upsert_rows(db, rows)
    rows[0]["close"] = 3100.0
    sync_index.upsert_rows(db, rows)
    db.expire_all()
    r = db.execute(select(SaIndexQuote).where(SaIndexQuote.index_code == SENTINEL)).scalar_one()
    assert float(r.close) == 3100.0


def test_drops_invalid_rows(cleanup):
    rows = [
        {"index_code": SENTINEL, "trade_date": "2026-07-28", "close": 1.0},
        {"index_code": None, "trade_date": "2026-07-29"},  # missing code
        {"index_code": SENTINEL, "trade_date": None},  # missing date
    ]
    assert sync_index.upsert_rows(cleanup, rows) == 1


def test_sync_one_uses_client(monkeypatch, cleanup):
    db = cleanup
    monkeypatch.setattr(
        sync_index.akshare_client, "fetch_index_quotes",
        lambda symbol, name="": [{"index_code": symbol, "index_name": name, "trade_date": "2026-07-28", "close": 1.0}],
    )
    assert sync_index.sync_one(db, SENTINEL, "测试") == 1


def test_major_indices_codes():
    """Index codes carry exchange prefixes to avoid stock-code collision."""
    codes = [c for c, _ in sync_index.MAJOR_INDICES]
    assert codes == ["sh000001", "sz399001", "sz399006"]
