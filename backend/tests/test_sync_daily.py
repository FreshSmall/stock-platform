"""Tests for app.data.sync_daily against the LIVE ``stock_analysis`` DB.

SAFETY: these tests WRITE to ``daily_prices`` (the prod schema). To stay safe:
1. ``akshare`` is always mocked — no real network fetch.
2. Every test writes only under a sentinel ``stock_code`` (``ZZTEST``) that does
   not correspond to any real A-share.
3. Every test deletes its sentinel rows in a ``finally`` block, so the prod DB
   is left clean even if an assertion fails.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select

from app.data import sync_daily
from app.models.stock import DailyPrice

# Sentinel code: not a valid A-share code (those start with 0/3/6), so it can
# never collide with real data. All tests write under this code only.
SENTINEL = "ZZTEST"


@pytest.fixture
def cleanup_sentinel(db_session):
    """Ensure no sentinel rows survive the test, even on assertion failure.

    Yields the session, then scrubs all rows for ``SENTINEL`` at teardown.
    Runs at the start too, in case a previous crashed test left litter.
    """
    db_session.execute(
        delete(DailyPrice).where(DailyPrice.stock_code == SENTINEL)
    )
    db_session.commit()
    try:
        yield db_session
    finally:
        db_session.execute(
            delete(DailyPrice).where(DailyPrice.stock_code == SENTINEL)
        )
        db_session.commit()


def _make_rows(
    closes: list[float],
    *,
    base: date | None = None,
    stock_code: str = SENTINEL,
) -> list[dict]:
    """Build N synthetic daily-quote dicts with given close values.

    Dates run back from ``base`` (default today) one day per row.
    """
    base = base or date.today()
    rows = []
    for i, close in enumerate(closes):
        rows.append(
            {
                "stock_code": stock_code,
                "trade_date": (base - timedelta(days=i)).isoformat(),
                "open": close,
                "close": close,
                "high": close + 1.0,
                "low": close - 1.0,
                "volume": 1000 + i,
                "amount": close * 1000,
                "pct_change": 1.0,
                "turnover": 0.5,
            }
        )
    return rows


def test_upsert_daily_rows_inserts_then_updates(
    monkeypatch, cleanup_sentinel
) -> None:
    """UPSERT inserts new rows, then updates them in place on re-run."""
    db = cleanup_sentinel

    rows = _make_rows([100.0, 101.0])

    written = sync_daily.upsert_daily_rows(db, rows)
    assert written == 2

    # Confirm 2 rows landed for the sentinel.
    fetched = db.execute(
        select(DailyPrice).where(DailyPrice.stock_code == SENTINEL)
    ).scalars().all()
    assert len(fetched) == 2
    closes_after_insert = sorted(float(r.close) for r in fetched)
    assert closes_after_insert == [100.0, 101.0]

    # Re-run with DIFFERENT closes → UPSERT updates, no new rows.
    rows_v2 = _make_rows([200.0, 201.0])
    written2 = sync_daily.upsert_daily_rows(db, rows_v2)
    assert written2 == 2  # still 2 written (the UPDATE branch)

    # SessionLocal has expire_on_commit=False, so the ORM identity map still
    # holds the stale 100/101 instances from the first read. Drop them before
    # re-reading so we see the post-UPDATE values.
    db.expire_all()
    fetched2 = db.execute(
        select(DailyPrice).where(DailyPrice.stock_code == SENTINEL)
    ).scalars().all()
    assert len(fetched2) == 2  # count unchanged → idempotent
    closes_after_update = sorted(float(r.close) for r in fetched2)
    assert closes_after_update == [200.0, 201.0]  # values updated


def test_sync_one_stock_uses_client(
    monkeypatch, cleanup_sentinel
) -> None:
    """sync_one_stock fetches via akshare_client and writes the returned rows."""
    db = cleanup_sentinel

    sample = _make_rows([50.0, 51.0])

    def fake_fetch(code, start_date, end_date, max_bars=400):
        assert code == SENTINEL
        assert max_bars == 400  # default for the incremental path
        # Dates are passed through but our fake ignores them.
        return sample

    monkeypatch.setattr(
        sync_daily.akshare_client,
        "fetch_daily_quotes",
        fake_fetch,
    )

    n = sync_daily.sync_one_stock(db, SENTINEL, "20260101", "20260102")
    assert n == 2

    fetched = db.execute(
        select(DailyPrice).where(DailyPrice.stock_code == SENTINEL)
    ).scalars().all()
    assert len(fetched) == 2


def test_validate_filters_invalid_rows(
    monkeypatch, cleanup_sentinel
) -> None:
    """Rows failing validation (missing close) are dropped; only valid kept."""
    db = cleanup_sentinel

    # 2 valid + 1 invalid (close=None) + 1 invalid (missing code).
    valid_rows = _make_rows([10.0, 11.0])
    invalid_rows = [
        {
            "stock_code": SENTINEL,
            "trade_date": "2026-01-03",
            "close": None,  # invalid → dropped
        },
        {
            "stock_code": None,
            "trade_date": "2026-01-04",
            "close": 12.0,  # invalid (no code) → dropped
        },
    ]

    written = sync_daily.upsert_daily_rows(db, valid_rows + invalid_rows)
    assert written == 2  # only the 2 valid rows

    fetched = db.execute(
        select(DailyPrice).where(DailyPrice.stock_code == SENTINEL)
    ).scalars().all()
    assert len(fetched) == 2


def test_upsert_empty_rows_noop(cleanup_sentinel) -> None:
    """UPSERT of an empty list writes nothing and returns 0."""
    db = cleanup_sentinel
    written = sync_daily.upsert_daily_rows(db, [])
    assert written == 0
    fetched = db.execute(
        select(DailyPrice).where(DailyPrice.stock_code == SENTINEL)
    ).scalars().all()
    assert fetched == []


def test_to_date_handles_akshare_string() -> None:
    """_to_date parses the 'YYYY-MM-DD' string akshare actually returns."""
    assert sync_daily._to_date("2026-07-28") == date(2026, 7, 28)
    assert sync_daily._to_date(None) is None
    assert sync_daily._to_date("not-a-date") is None
