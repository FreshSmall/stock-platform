"""Tests for per-code failure isolation in ``scheduler._sync_codes``.

Observed 2026-09-18: one transient MySQL disconnect left the shared session
in a failed-transaction state, and every later code then died instantly with
"Can't reconnect until invalid transaction is rolled back" (~1100 codes lost).
``_reset_session`` must roll the session back so the loop keeps its one-code
blast radius; even a raising rollback (DB hard-down) must not abort the loop.
"""

from app import scheduler
from app.core.config import settings


class _RollbackRecorder:
    """Session stub: counts rollback() calls, nothing else is touched."""

    def __init__(self):
        self.rollbacks = 0

    def rollback(self):
        self.rollbacks += 1


def test_legacy_failure_rolls_back_and_continues(monkeypatch):
    """A failing code rolls the session back; later codes still run."""
    monkeypatch.setattr(settings, "kline_source", "legacy")
    monkeypatch.setattr(settings, "kline_rebuild_enabled", False)

    attempted = []

    def fake_sync(db, code, start, end):
        attempted.append(code)
        if code == "600000":
            raise RuntimeError("lost connection to MySQL")
        return 1

    monkeypatch.setattr("app.data.sync_daily.sync_one_stock", fake_sync)

    db = _RollbackRecorder()
    rows, failed = scheduler._sync_codes(
        db, ["600000", "600001"], "20260911", "20260918"
    )

    assert attempted == ["600000", "600001"]  # loop survived the first failure
    assert rows == 1
    assert failed == ["600000"]
    assert db.rollbacks == 1


def test_v2_failure_rolls_back(monkeypatch):
    monkeypatch.setattr(settings, "kline_source", "v2")

    def fake_sync(db, code, start, end):
        raise RuntimeError("lost connection to MySQL")

    monkeypatch.setattr("app.data.sync_kline.sync_one_stock_v2", fake_sync)

    db = _RollbackRecorder()
    rows, failed = scheduler._sync_codes(db, ["600000"], "20260911", "20260918")

    assert rows == 0
    assert failed == ["600000"]
    assert db.rollbacks == 1


def test_rollback_failure_does_not_abort_loop(monkeypatch):
    """Even a raising rollback (DB hard-down) keeps the loop per-code."""
    monkeypatch.setattr(settings, "kline_source", "legacy")
    monkeypatch.setattr(settings, "kline_rebuild_enabled", False)

    attempted = []

    def fake_sync(db, code, start, end):
        attempted.append(code)
        raise RuntimeError("lost connection to MySQL")

    monkeypatch.setattr("app.data.sync_daily.sync_one_stock", fake_sync)

    class _BrokenRollback:
        def rollback(self):
            raise RuntimeError("db hard down")

    rows, failed = scheduler._sync_codes(
        _BrokenRollback(), ["600000", "600001"], "20260911", "20260918"
    )

    assert attempted == ["600000", "600001"]
    assert failed == ["600000", "600001"]
