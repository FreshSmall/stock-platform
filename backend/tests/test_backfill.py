"""Tests for startup gap detection + back-fill (app.data.backfill).

detect_gap tests mock latest_complete_trade_date (and settled_counts for the
completeness cases) so they don't depend on the real DB's data. The 17:00
eligibility rule for "today" is pinned by passing an explicit ``now``.
backfill_daily_k is DB-backed with the akshare client mocked, writing under
sentinel codes.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import delete

from app.data import backfill
from app.models.stock import DailyPrice, StockPool


def _insert_complete(db, code, d):
    """Insert a daily_prices row WITH pct_change (counts as 'settled')."""
    db.add(
        DailyPrice(
            stock_code=code, trade_date=d, open=Decimal("10"), close=Decimal("10"),
            high=Decimal("11"), low=Decimal("9"), volume=100, amount=Decimal("1000"),
            pct_change=Decimal("1.0"),
        )
    )


# --- detect_gap (mocked baseline, pure logic) -----------------------------
#
# NOTE: every test patches BOTH latest_complete_trade_date and settled_counts.
# The real shared DB has genuinely truncated days (2026-07-28/29), and the
# completeness check is supposed to see them — tests that only mock the max
# date would depend on that live data.


def _clean_counts(latest: date, days: int = 3) -> dict[date, int]:
    """Full-market row counts for the ``days`` dates ending at ``latest``."""
    return {latest - timedelta(days=i): 4168 for i in range(days)}


def test_detect_gap_no_baseline_returns_empty(db_session):
    """No baseline data → nothing to back-fill against."""
    with patch.object(backfill, "latest_complete_trade_date", return_value=None):
        assert backfill.detect_gap(db_session, today=date(2026, 7, 31)) == []


def test_detect_gap_up_to_date_returns_empty(db_session):
    """Latest complete date == today → no gap."""
    with (
        patch.object(backfill, "latest_complete_trade_date", return_value=date(2026, 7, 31)),
        patch.object(backfill, "settled_counts", return_value=_clean_counts(date(2026, 7, 31))),
    ):
        gap = backfill.detect_gap(
            db_session, today=date(2026, 7, 31), now=datetime(2026, 7, 31, 23, 0)
        )
        assert gap == []


def test_detect_gap_finds_missing_weekdays(db_session):
    """Latest=Wed 7/29, today=Fri 7/31 → missing Thu 7/30, Fri 7/31."""
    evening = datetime(2026, 7, 31, 23, 0)
    with (
        patch.object(backfill, "latest_complete_trade_date", return_value=date(2026, 7, 29)),
        patch.object(backfill, "settled_counts", return_value=_clean_counts(date(2026, 7, 29))),
    ):
        gap = backfill.detect_gap(db_session, today=date(2026, 7, 31), now=evening)
        assert gap == [date(2026, 7, 30), date(2026, 7, 31)]


def test_detect_gap_skips_weekends(db_session):
    """Latest=Fri 7/24, today=Mon 7/27 → only Mon 7/27 missing (skip Sat/Sun)."""
    evening = datetime(2026, 7, 27, 23, 0)
    with (
        patch.object(backfill, "latest_complete_trade_date", return_value=date(2026, 7, 24)),
        patch.object(backfill, "settled_counts", return_value=_clean_counts(date(2026, 7, 24))),
    ):
        gap = backfill.detect_gap(db_session, today=date(2026, 7, 27), now=evening)  # Monday
        assert gap == [date(2026, 7, 27)]


def test_detect_gap_capped_at_max(db_session):
    """A huge gap is capped at MAX_BACKFILL_DAYS."""
    evening = datetime(2026, 7, 31, 23, 0)
    with (
        patch.object(backfill, "latest_complete_trade_date", return_value=date(2020, 1, 1)),
        patch.object(backfill, "settled_counts", return_value=_clean_counts(date(2020, 1, 1))),
    ):
        gap = backfill.detect_gap(db_session, today=date(2026, 7, 31), now=evening)
        assert len(gap) <= backfill.MAX_BACKFILL_DAYS


def test_detect_gap_excludes_today_before_settlement(db_session):
    """Before 17:00 "today" is not eligible — an early sync would upsert
    half-day bars, so the window must stop at yesterday."""
    morning = datetime(2026, 7, 31, 9, 0)
    with (
        patch.object(backfill, "latest_complete_trade_date", return_value=date(2026, 7, 29)),
        patch.object(backfill, "settled_counts", return_value=_clean_counts(date(2026, 7, 29))),
    ):
        gap = backfill.detect_gap(db_session, today=date(2026, 7, 31), now=morning)
        assert gap == [date(2026, 7, 30)]  # Fri 7/31 excluded


def test_detect_gap_includes_partially_synced_latest(db_session):
    """The 2026-08-14 scenario: latest day has settled bars but only ~37% of
    the baseline count → it must be re-synced, not treated as complete."""
    counts = {
        date(2026, 8, 12): 4167,
        date(2026, 8, 13): 4168,
        date(2026, 8, 14): 1546,  # partial — the truncated 17:30 run
    }
    evening = datetime(2026, 8, 14, 22, 31)  # the actual restart time
    with (
        patch.object(backfill, "latest_complete_trade_date", return_value=date(2026, 8, 14)),
        patch.object(backfill, "settled_counts", return_value=counts),
    ):
        gap = backfill.detect_gap(db_session, today=date(2026, 8, 14), now=evening)
        assert gap == [date(2026, 8, 14)]


def test_detect_gap_ignores_small_day_over_day_drift(db_session):
    """Counts within a few percent of the baseline are normal market drift
    (listings/suspensions) — those days must NOT be re-synced."""
    counts = {
        date(2026, 8, 12): 4160,
        date(2026, 8, 13): 4168,
        date(2026, 8, 14): 4162,
    }
    evening = datetime(2026, 8, 14, 23, 0)
    with (
        patch.object(backfill, "latest_complete_trade_date", return_value=date(2026, 8, 14)),
        patch.object(backfill, "settled_counts", return_value=counts),
    ):
        gap = backfill.detect_gap(db_session, today=date(2026, 8, 14), now=evening)
        assert gap == []


def test_latest_complete_trade_date_ignores_null_pct(db_session):
    """latest_complete_trade_date skips rows with NULL pct_change.

    NOTE: can't assert an absolute date here (the shared real DB has settled
    bars up to the latest trading day, which would mask a sentinel). Instead we
    verify the rule directly: insert a sentinel row for a FAR-future date with
    NULL pct_change and confirm it does NOT become the baseline.
    """
    db = db_session
    baseline_before = backfill.latest_complete_trade_date(db)
    far_future = baseline_before + timedelta(days=365 * 5) if baseline_before else date(2099, 12, 31)
    db.add(  # far-future row, UNSETTLED (no pct_change) — must be ignored
        DailyPrice(
            stock_code="ZZBFL", trade_date=far_future,
            open=Decimal("10"), close=Decimal("10"), high=Decimal("11"),
            low=Decimal("9"), volume=100, amount=Decimal("1000"), pct_change=None,
        )
    )
    db.commit()
    try:
        # baseline unchanged → the far-future unsettled row was ignored
        assert backfill.latest_complete_trade_date(db) == baseline_before
    finally:
        db.execute(delete(DailyPrice).where(DailyPrice.stock_code == "ZZBFL"))
        db.commit()


# --- backfill_daily_k (DB-backed, mocked) ---------------------------------


@pytest.fixture
def pool_with_sentinel(db_session):
    """Insert a stock_pool snapshot with one sentinel code, clean up after.

    Cleans up at setup too, in case a prior crashed run left litter.
    """
    db = db_session
    db.execute(delete(StockPool).where(StockPool.stock_code == "ZZBFS"))
    db.execute(delete(DailyPrice).where(DailyPrice.stock_code == "ZZBFS"))
    db.commit()
    db.add(StockPool(
        pool_name="default", trade_date=date(2026, 6, 19),
        stock_code="ZZBFS", stock_name="BACKFILL_TEST",
    ))
    db.commit()
    try:
        yield db
    finally:
        db.execute(delete(StockPool).where(StockPool.stock_code == "ZZBFS"))
        db.execute(delete(DailyPrice).where(DailyPrice.stock_code == "ZZBFS"))
        db.commit()


def test_backfill_no_gap_noop(db_session):
    """When there's no gap, back-fill writes nothing."""
    with patch.object(backfill, "detect_gap", return_value=[]):
        assert backfill.backfill_daily_k(db_session) == 0


def test_backfill_syncs_missing_days(pool_with_sentinel, monkeypatch):
    """Back-fill calls sync_one_stock over the gap window for each pool code."""
    db = pool_with_sentinel
    captured = {}

    def fake_sync(dbsess, code, start, end):
        captured.setdefault("calls", []).append((code, start, end))
        return 1  # don't actually write — avoid polluting the shared DB

    # Force the pool enumeration to see ONLY the sentinel code, so the test is
    # isolated from the real 4000+ stock_pool snapshot.
    monkeypatch.setattr(backfill.sync_daily, "sync_one_stock", fake_sync)
    monkeypatch.setattr(backfill, "_enumerate_pool_codes", lambda db: ["ZZBFS"])
    rows = backfill.backfill_daily_k(db, missing_days=[date(2026, 7, 30), date(2026, 7, 31)])
    assert rows == 1
    # one call per code, spanning the whole gap window in a single request
    assert captured["calls"] == [("ZZBFS", "20260730", "20260731")]


def test_backfill_resilient_to_per_code_failure(pool_with_sentinel, monkeypatch):
    """A failing code is skipped; the run still completes without raising."""
    db = pool_with_sentinel

    def boom(dbsess, code, start, end):
        raise RuntimeError("network down")

    monkeypatch.setattr(backfill.sync_daily, "sync_one_stock", boom)
    monkeypatch.setattr(backfill, "_enumerate_pool_codes", lambda db: ["ZZBFS"])
    rows = backfill.backfill_daily_k(db, missing_days=[date(2026, 7, 31)])
    assert rows == 0


def test_backfill_on_startup_swallows_errors():
    """The startup entrypoint must never raise, even on DB failure."""
    with patch.object(backfill, "backfill_daily_k", side_effect=RuntimeError("boom")):
        # should not raise
        backfill.backfill_on_startup()
