"""Tests for app.data.sync_minute against the LIVE ``stock_analysis`` DB.

SAFETY (same convention as test_sync_daily): akshare is always mocked; writes
go only under a sentinel ``stock_code`` (``ZZMIN``) that is not a valid A-share;
every test scrubs its rows in a ``finally``.
"""

from datetime import datetime

import pytest
from sqlalchemy import delete, select

from app.data import sync_minute
from app.models.market_data import SaMinutePrice

SENTINEL = "ZZMIN"


@pytest.fixture
def cleanup(db_session):
    db_session.execute(delete(SaMinutePrice).where(SaMinutePrice.stock_code == SENTINEL))
    db_session.commit()
    try:
        yield db_session
    finally:
        db_session.execute(delete(SaMinutePrice).where(SaMinutePrice.stock_code == SENTINEL))
        db_session.commit()


def _row(period=5, tt="2026-07-28 14:30", close=100.0):
    return {
        "stock_code": SENTINEL,
        "period": period,
        "trade_time": tt,
        "trade_date": "2026-07-28",
        "open": close,
        "close": close,
        "high": close + 1,
        "low": close - 1,
        "volume": 1000,
        "amount": close * 1000,
    }


def test_upsert_inserts_then_updates(cleanup):
    db = cleanup
    assert sync_minute.upsert_minute_rows(db, [_row()]) == 1
    fetched = db.execute(
        select(SaMinutePrice).where(SaMinutePrice.stock_code == SENTINEL)
    ).scalars().all()
    assert len(fetched) == 1
    assert fetched[0].trade_time == datetime(2026, 7, 28, 14, 30)

    # re-run with different close → UPSERT updates, no new row
    assert sync_minute.upsert_minute_rows(db, [_row(close=200.0)]) == 1
    db.expire_all()
    fetched = db.execute(
        select(SaMinutePrice).where(SaMinutePrice.stock_code == SENTINEL)
    ).scalars().all()
    assert len(fetched) == 1
    assert float(fetched[0].close) == 200.0


def test_sync_one_stock_uses_client(monkeypatch, cleanup):
    db = cleanup
    monkeypatch.setattr(
        sync_minute.akshare_client, "fetch_minute_quotes",
        lambda code, period=5: [_row(period=5), _row(period=5, tt="2026-07-28 14:35")],
    )
    n = sync_minute.sync_one_stock(db, SENTINEL, period=5)
    assert n == 2


def test_drops_rows_missing_key_fields(cleanup):
    db = cleanup
    rows = [
        _row(),
        {"stock_code": SENTINEL, "trade_time": None, "period": 5},  # missing time
        {"stock_code": None, "trade_time": "2026-07-28 14:40", "period": 5},  # missing code
    ]
    assert sync_minute.upsert_minute_rows(db, rows) == 1


def test_empty_rows_noop(cleanup):
    assert sync_minute.upsert_minute_rows(cleanup, []) == 0


def test_to_dt_parses_akshare_string():
    assert sync_minute._to_dt("2026-07-28 14:30") == datetime(2026, 7, 28, 14, 30)
    assert sync_minute._to_dt("2026-07-28 14:30:00") == datetime(2026, 7, 28, 14, 30)
    assert sync_minute._to_dt(None) is None
    assert sync_minute._to_dt("garbage") is None
