"""Tests for the 23:00 retry's completeness self-check (app.scheduler).

The self-check reads per-date settled row counts via backfill.settled_counts.
Rather than stand up data in the shared real DB, SessionLocal is patched with
a stub session whose single ``execute`` returns the (date, count) rows the
real query would produce — ``settled_counts`` only calls ``.all()`` on it.
"""

from datetime import date, timedelta
from unittest.mock import patch

from app import scheduler


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    """Session stub: one canned SELECT result, close() is a no-op."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, *_args, **_kwargs):
        return _FakeResult(self._rows)

    def close(self):
        pass


def _self_check(rows):
    with patch("app.core.database.SessionLocal", return_value=_FakeDB(rows)):
        return scheduler._today_looks_incomplete()


def test_self_check_flags_partial_today():
    """Today at ~37% of yesterday's count → incomplete (the 8/14 scenario)."""
    today = date.today()
    rows = [(today - timedelta(days=1), 4168), (today, 1546)]
    assert _self_check(rows) is True


def test_self_check_flags_missing_today():
    """17:30 run died before writing anything → today absent → incomplete."""
    today = date.today()
    rows = [(today - timedelta(days=1), 4168)]
    assert _self_check(rows) is True


def test_self_check_passes_complete_today():
    today = date.today()
    rows = [(today - timedelta(days=2), 4160), (today - timedelta(days=1), 4168), (today, 4167)]
    assert _self_check(rows) is False


def test_self_check_no_prior_history_is_not_incomplete():
    """Nothing to compare against (fresh DB) → False, don't fire blind."""
    today = date.today()
    rows = [(today, 100)]
    assert _self_check(rows) is False


def test_retry_reruns_full_sync_when_incomplete():
    """Incomplete today → the retry delegates to the full 17:30 sync."""
    calls = []
    with (
        patch.object(scheduler, "_today_looks_incomplete", return_value=True),
        patch.object(scheduler, "run_daily_sync", side_effect=lambda: calls.append(1)),
    ):
        scheduler._last_run_failed_codes = []
        scheduler.run_daily_sync_retry()
    assert calls == [1]


def test_retry_skips_when_complete_and_no_failures():
    """Complete today + no failed codes → no-op, no full run."""
    calls = []
    with (
        patch.object(scheduler, "_today_looks_incomplete", return_value=False),
        patch.object(scheduler, "run_daily_sync", side_effect=lambda: calls.append(1)),
    ):
        scheduler._last_run_failed_codes = []
        scheduler.run_daily_sync_retry()
    assert calls == []


def test_retry_self_check_failure_falls_through_to_replay():
    """A crashing self-check must not kill the retry's replay role."""
    scheduler._last_run_failed_codes = ["600000"]
    synced = []

    def fake_sync(db, codes, start, end):
        synced.extend(codes)
        return 1, []

    with (
        patch.object(scheduler, "_today_looks_incomplete", side_effect=RuntimeError("db down")),
        patch.object(scheduler, "_sync_codes", side_effect=fake_sync),
        patch("app.core.database.SessionLocal", return_value=_FakeDB([])),
    ):
        scheduler.run_daily_sync_retry()
    assert synced == ["600000"]
    assert scheduler._last_run_failed_codes == []  # cleared after the run
