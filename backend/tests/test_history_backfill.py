"""Tests for the multi-year history back-fill (app.data.history_backfill).

Pure-logic tests cover the chunking / target-start maths. DB-backed tests use
sentinel ``9988xx`` codes and mock the network layer (``sync_stock_history``)
so nothing hits the data sources; state rows are cleaned up in ``finally``.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import delete, select

from app.data import history_backfill as hb
from app.models.market_data import SaHistorySyncState
from app.models.stock import DailyPrice

_SENTINELS = ["998801", "998802", "998803", "998804", "998805"]


def _cleanup(db):
    db.execute(delete(SaHistorySyncState).where(SaHistorySyncState.stock_code.in_(_SENTINELS)))
    db.execute(delete(DailyPrice).where(DailyPrice.stock_code.in_(_SENTINELS)))
    db.commit()


def _scoped_pending(db, limit):
    """``next_pending`` restricted to sentinel codes.

    The REAL state table holds thousands of production rows (seeded by the
    live back-fill), and ``next_pending`` orders by attempts/id across the
    whole table — without this filter a test batch would process (and, with
    the sync mocked, corrupt) real pending stocks.
    """
    rows = (
        db.execute(
            select(SaHistorySyncState)
            .where(
                SaHistorySyncState.stock_code.in_(_SENTINELS),
                SaHistorySyncState.status == "pending",
                SaHistorySyncState.attempts < hb.MAX_ATTEMPTS,
            )
            .order_by(SaHistorySyncState.attempts.asc(), SaHistorySyncState.id.asc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return rows


# --- pure logic ------------------------------------------------------------


def test_chunk_windows_split_five_years_into_400d_chunks():
    start, end = date(2021, 8, 19), date(2026, 8, 19)
    windows = hb.chunk_windows(start, end)
    # 1826 calendar days → ceil(1826/400) = 5 chunks
    assert len(windows) == 5
    # contiguous & ascending, covering the whole span exactly
    assert windows[0][0] == start and windows[-1][1] == end
    for (_, ce), (ns, _) in zip(windows, windows[1:]):
        assert ns == ce + timedelta(days=1)
    # every chunk ≤ 400 days
    assert all((ce - cs).days + 1 <= 400 for cs, ce in windows)
    # the 400-day span keeps the bar estimate (span*7/5+50=610) under the
    # ~640-bar server clamp
    assert 400 * 7 / 5 + 50 <= hb.MAX_BARS_PER_REQ


def test_chunk_windows_empty_when_reversed():
    assert hb.chunk_windows(date(2026, 1, 1), date(2020, 1, 1)) == []


def test_target_start_never_earlier_than_listing():
    hist_start = hb.history_start()
    assert hb.target_start_for(None) == hist_start
    old = hist_start - timedelta(days=365)
    assert hb.target_start_for(old) == hist_start
    recent = hist_start + timedelta(days=365)
    assert hb.target_start_for(recent) == recent  # 2023 listing: target is IPO date


def test_expected_bars_ballpark_for_five_years():
    # 1826 calendar days ≈ 1304 weekdays × 0.93 holiday trim ≈ 1213
    assert 1150 <= hb.expected_bars(date(2021, 8, 19), date(2026, 8, 19)) <= 1280


def test_is_complete_catches_mid_window_holes():
    target, end = date(2021, 8, 19), date(2026, 8, 19)
    ok = hb.expected_bars(target, end)
    # full depth + full count → done
    assert hb._is_complete(target, end, target, ok) is True
    # earliest reached but 28% of bars missing (observed on 600519) → NOT done
    assert hb._is_complete(target, end, target, int(ok * 0.72)) is False
    # full count but earliest late beyond grace → NOT done
    assert hb._is_complete(target, end, target + timedelta(days=90), ok) is False
    # no data at all → NOT done
    assert hb._is_complete(target, end, None, 0) is False


def test_relaxed_gate_accepts_late_listing_measured_from_own_start():
    """NULL-list_date stocks: target unreachable, coverage continuous from
    own first bar → accepted on the relaxed rule (mid-holes still rejected)."""
    target, end = date(2021, 8, 19), date(2026, 8, 19)
    listed = date(2024, 9, 20)  # e.g. 603091: pool snapshot lacks list_date
    ok = hb.expected_bars(listed, end)
    assert hb._complete_from_own_start(target, end, listed, ok) is True
    assert hb._complete_from_own_start(target, end, listed, int(ok * 0.7)) is False
    assert hb._complete_from_own_start(target, end, None, 0) is False


def test_quiet_window_covers_daily_sync_hours():
    assert hb._in_quiet_window(datetime(2026, 8, 19, 17, 30)) is True
    assert hb._in_quiet_window(datetime(2026, 8, 19, 18, 44)) is True
    assert hb._in_quiet_window(datetime(2026, 8, 19, 12, 0)) is False
    assert hb._in_quiet_window(datetime(2026, 8, 19, 19, 0)) is False


# --- DB-backed (sentinel codes, network mocked) -----------------------------


def _insert_state(db, code, target, status="pending", attempts=0):
    db.add(
        SaHistorySyncState(
            stock_code=code, target_start=target,
            status=status, attempts=attempts,
        )
    )
    db.commit()


def _get_state(db, code) -> SaHistorySyncState:
    return db.execute(
        select(SaHistorySyncState).where(SaHistorySyncState.stock_code == code)
    ).scalar_one()


def test_ensure_state_seeds_rows_and_shortcuts_done(db_session):
    """A code whose stored history already reaches the target is seeded done."""
    target = hb.history_start()
    try:
        # 998801 already has a bar at target+5d → seeded as done, no fetch.
        # ``expected_bars`` is stubbed to 1 so a single bar passes the
        # completeness gate (the real gate needs ~1200 bars — see the pure
        # ``_is_complete`` tests).
        db_session.add(
            DailyPrice(
                stock_code="998801", trade_date=target + timedelta(days=5),
                open=Decimal("10"), close=Decimal("10"), high=Decimal("11"),
                low=Decimal("9"), volume=100, amount=Decimal("1000"),
                pct_change=Decimal("1.0"),
            )
        )
        db_session.commit()
        pool = {"998801": None, "998802": target + timedelta(days=365)}
        with patch.object(hb, "expected_bars", lambda s, e: 1):
            assert hb.ensure_state(db_session, pool=pool) == 2

        s1 = _get_state(db_session, "998801")
        assert s1.status == "done"
        assert s1.earliest_bar == target + timedelta(days=5)
        s2 = _get_state(db_session, "998802")
        assert s2.status == "pending"
        assert s2.target_start == target + timedelta(days=365)  # IPO clamp

        # idempotent: the second pass inserts nothing
        assert hb.ensure_state(db_session, pool=pool) == 0
    finally:
        _cleanup(db_session)


def test_run_history_batch_done_and_attempt_paths(db_session):
    """Reached target → done; history still short → attempts+1, stays pending."""
    target = hb.history_start()
    try:
        _insert_state(db_session, "998803", target)
        _insert_state(db_session, "998804", target)

        calls = []

        def fake_sync(db, code, start, end):
            calls.append((code, start, end))
            if code == "998803":
                return 300, target + timedelta(days=3)  # within grace → done
            return 50, target + timedelta(days=200)  # way short of target

        with (
            patch.object(hb, "sync_stock_history", side_effect=fake_sync),
            patch.object(hb, "next_pending", _scoped_pending),
            # full bar count for the done-path code; the real table has none
            patch.object(hb, "_bar_counts_bulk", lambda db, codes: {c: 10**6 for c in codes}),
            patch("app.data.history_backfill.time.sleep"),
        ):
            summary = hb.run_history_batch(db_session, batch_size=10)

        assert summary["synced"] == 2 and summary["done"] == 1 and summary["failed"] == 0
        assert _get_state(db_session, "998803").status == "done"
        s4 = _get_state(db_session, "998804")
        assert s4.status == "pending" and s4.attempts == 1
        # every sync covers the per-stock target
        assert all(start == target for _, start, _ in calls)
    finally:
        _cleanup(db_session)


def test_batch_failures_park_after_max_attempts_then_reset(db_session):
    target = hb.history_start()
    try:
        _insert_state(db_session, "998805", target)
        with (
            patch.object(hb, "sync_stock_history", side_effect=RuntimeError("boom")),
            patch.object(hb, "next_pending", _scoped_pending),
            patch("app.data.history_backfill.time.sleep"),
        ):
            for _ in range(hb.MAX_ATTEMPTS):
                hb.run_history_batch(db_session, batch_size=10)
            s = _get_state(db_session, "998805")
            assert s.status == "failed" and s.attempts == hb.MAX_ATTEMPTS
            assert "boom" in s.last_error

            # parked codes leave the pending queue
            assert _scoped_pending(db_session, 10) == []

            # admin reset puts it back in rotation
            assert hb.reset_failed(db_session) == 1
            s = _get_state(db_session, "998805")
            assert s.status == "pending" and s.attempts == 0
    finally:
        _cleanup(db_session)


def test_history_progress_counts_statuses(db_session):
    """Asserts DELTAS against the live table — production rows exist alongside."""
    target = hb.history_start()
    try:
        before = hb.history_progress(db_session)
        _insert_state(db_session, "998803", target, status="done")
        _insert_state(db_session, "998804", target, status="done")
        _insert_state(db_session, "998805", target)
        after = hb.history_progress(db_session)
        assert after["done"] - before["done"] == 2
        assert after["pending"] - before["pending"] == 1
        assert after["failed"] == before["failed"]
        assert after["total"] - before["total"] == 3
    finally:
        _cleanup(db_session)


def test_relaxed_gate_only_applies_from_second_attempt(db_session):
    """A late-listing stock fails pass 1, completes via the relaxed rule on pass 2."""
    target = hb.history_start()
    try:
        _insert_state(db_session, "998803", target)  # attempts=0
        late_start = target + timedelta(days=900)    # listed ~2.5y into the window
        bars = hb.expected_bars(late_start, date.today())  # continuous since listing

        with (
            patch.object(hb, "sync_stock_history", return_value=(bars, late_start)),
            patch.object(hb, "next_pending", _scoped_pending),
            patch.object(hb, "_bar_counts_bulk", lambda db, codes: {c: bars for c in codes}),
            patch("app.data.history_backfill.time.sleep"),
        ):
            first = hb.run_history_batch(db_session, batch_size=10)
            s = _get_state(db_session, "998803")
            assert first["done"] == 0 and s.status == "pending" and s.attempts == 1
            second = hb.run_history_batch(db_session, batch_size=10)
            s = _get_state(db_session, "998803")
            assert second["done"] == 1 and s.status == "done"
    finally:
        _cleanup(db_session)


def test_tick_respects_disable_flag_and_quiet_window():
    """Both skip paths return before touching the DB."""
    from types import SimpleNamespace

    disabled = SimpleNamespace(history_backfill_enabled=False)
    with patch.object(hb, "settings", disabled):
        assert hb.tick() == {"skipped": "disabled"}

    enabled = SimpleNamespace(history_backfill_enabled=True)
    with (
        patch.object(hb, "settings", enabled),
        patch.object(hb, "_in_quiet_window", return_value=True),
    ):
        assert hb.tick() == {"skipped": "quiet_window"}
