"""Tests for the Tencent-host stability layer added 2026-08-30.

Covers the three defenses around :func:`akshare_client._tencent_get`
(pacing, per-host 501 cooldown, session rebuild) plus the scheduler's
trade-day gating — the fixes for the 2026-08-26..29 incident where the
classic ``web.ifzq.gtimg.cn`` WAF 501-banned sustained per-stock polling
and a weekend retry misfire doubled the request pressure.
"""

import time
from datetime import date, datetime
from unittest.mock import Mock, patch

import pytest

from app.data import akshare_client as ac
from app import scheduler


@pytest.fixture(autouse=True)
def _clean_tencent_state():
    """Isolate the module-level pacing/cooldown state between tests."""
    ac._waf_cooldown_until.clear()
    yield
    ac._waf_cooldown_until.clear()


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    """Counts ``.get`` calls; serves queued responses (default 200/empty)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0) if self.responses else _FakeResponse()


def _run_tencent_get(fake_session, url="https://web.ifzq.gtimg.cn/x", params=None):
    """Call _tencent_get with a fake session and no pacing wait."""
    with (
        patch.object(ac, "_http", fake_session),
        patch.object(ac, "_tencent_last_ts", time.time()),
    ):
        return ac._tencent_get(url, params=params or {}, timeout=1)


def test_tencent_get_paces_requests():
    """Successive calls are >= the minimum interval apart (WAF pacemaker)."""
    fake = _FakeSession([])
    with patch.object(ac, "_http", fake):
        t0 = time.time()
        ac._tencent_get("https://web.ifzq.gtimg.cn/x", params={}, timeout=1)
        ac._tencent_get("https://web.ifzq.gtimg.cn/x", params={}, timeout=1)
        elapsed = time.time() - t0
    assert elapsed >= ac._TENCENT_MIN_INTERVAL_SEC


def test_tencent_get_501_sets_host_cooldown():
    """A 501 marks the host untouchable: the next call skips the network."""
    fake = _FakeSession([_FakeResponse(status_code=501)])
    assert _run_tencent_get(fake) is None

    probe = _FakeSession([])
    assert _run_tencent_get(probe) is None
    assert probe.calls == []  # cooldown means not even one request goes out


def test_tencent_get_cooldown_expires():
    """After the cooldown window the host is queried again."""
    fake = _FakeSession([_FakeResponse(status_code=501)])
    assert _run_tencent_get(fake) is None

    probe = _FakeSession([])
    with patch.object(ac, "_waf_cooldown_until", {}):
        assert _run_tencent_get(probe) is not None
        assert len(probe.calls) == 1


def test_tencent_get_501_rebuilds_session():
    """A 501 triggers the session rebuild (fresh pool + rotated UA)."""
    rebuild = Mock()
    fake = _FakeSession([_FakeResponse(status_code=501)])
    with patch.object(ac, "_reset_http_session", rebuild):
        assert _run_tencent_get(fake) is None
    rebuild.assert_called_once()


def test_reset_http_session_rotates_ua():
    """The rebuild swaps the shared session and cycles through the UA pool."""
    original = ac._http
    ac._reset_http_session()
    try:
        assert ac._http is not original
        assert ac._http.trust_env is False
        assert ac._http.headers["User-Agent"] in ac._USER_AGENTS
    finally:
        ac._http = original


def test_tencent_get_isolation_between_hosts():
    """A 501 on one host does not cool down the other Tencent host."""
    fake = _FakeSession([_FakeResponse(status_code=501)])
    assert _run_tencent_get(fake, url="https://web.ifzq.gtimg.cn/a") is None

    probe = _FakeSession([])
    assert _run_tencent_get(probe, url="https://proxy.finance.qq.com/b") is not None
    assert len(probe.calls) == 1


def test_fetch_daily_quotes_prefers_alt_host():
    """Source order: proxy.finance.qq.com (extended) first, classic host second."""
    calls = []

    def fake_fetch(symbol, start, end, url=None, extended=False, max_bars=400):
        calls.append({"url": url or ac._TENCENT_KLINE, "extended": extended})
        # First source answers → the other two must never be tried.
        return [{"stock_code": symbol}]

    with patch.object(ac, "_fetch_daily_quotes_tencent", side_effect=fake_fetch):
        rows = ac.fetch_daily_quotes("600519", "20260817", "20260828")
    assert rows == [{"stock_code": "600519"}]
    assert len(calls) == 1
    assert calls[0]["url"] == ac._TENCENT_KLINE_ALT
    assert calls[0]["extended"] is True


def test_fetch_daily_quotes_falls_through_when_primary_empty():
    """Primary returning [] (e.g. cooldown) → classic host is tried next."""
    calls = []

    def fake_fetch(symbol, start, end, url=None, extended=False, max_bars=400):
        effective = url or ac._TENCENT_KLINE
        calls.append(effective)
        return [{"stock_code": symbol}] if effective == ac._TENCENT_KLINE else []

    with patch.object(ac, "_fetch_daily_quotes_tencent", side_effect=fake_fetch):
        rows = ac.fetch_daily_quotes("600519", "20260817", "20260828")
    assert rows == [{"stock_code": "600519"}]
    assert calls == [ac._TENCENT_KLINE_ALT, ac._TENCENT_KLINE]


# --- trade-day gating (app.scheduler) ---------------------------------------


def test_is_trade_day_rejects_weekends():
    assert scheduler._is_trade_day(date(2026, 8, 29)) is False  # Saturday
    assert scheduler._is_trade_day(date(2026, 8, 30)) is False  # Sunday


def test_is_trade_day_uses_calendar_for_weekdays():
    with patch.object(scheduler, "_load_trade_calendar", return_value={date(2026, 10, 1)}):
        assert scheduler._is_trade_day(date(2026, 10, 1)) is True  # in calendar
        assert scheduler._is_trade_day(date(2026, 10, 2)) is False  # holiday, not in cal


def test_is_trade_day_falls_back_to_weekday_when_calendar_fails():
    with patch.object(scheduler, "_load_trade_calendar", return_value=None):
        assert scheduler._is_trade_day(date(2026, 10, 2)) is True  # assume trading


def test_run_daily_sync_skips_non_trade_day():
    """Non-trading day → no DB pool query, no sync, failed-codes cleared."""
    synced = []
    with (
        patch.object(scheduler, "_is_trade_day", return_value=False),
        patch.object(
            scheduler, "_sync_codes",
            side_effect=lambda *a: synced.append(a) or (0, []),
        ),
        patch("app.core.database.SessionLocal"),
    ):
        scheduler._last_run_failed_codes = ["600000"]
        scheduler.run_daily_sync()
    assert synced == []
    assert scheduler._last_run_failed_codes == []


def test_run_daily_sync_runs_on_trade_day():
    """Trading day → the pool is enumerated and the sync runs."""
    synced = []

    class _FakeResult:
        @staticmethod
        def scalar():
            return date(2026, 8, 28)

        @staticmethod
        def scalars():
            class _S:
                @staticmethod
                def all():
                    return ["600519"]

            return _S()

    class _FakeDB:
        def execute(self, *_a, **_k):
            return _FakeResult()

        def close(self):
            pass

    with (
        patch.object(scheduler, "_is_trade_day", return_value=True),
        patch.object(
            scheduler, "_sync_codes",
            side_effect=lambda db, codes, s, e: synced.extend(codes) or (0, []),
        ),
        patch("app.core.database.SessionLocal", return_value=_FakeDB()),
    ):
        scheduler.run_daily_sync()
    assert synced == ["600519"]


def test_fetch_trade_calendar_coerces_value_types():
    """Calendar values arrive as date/datetime/str depending on akshare."""
    class _DF:
        def __getitem__(self, key):
            assert key == "trade_date"
            return [date(2026, 9, 30), datetime(2026, 10, 1, 15), "2026-10-09"]

    with (
        patch.object(ac.ak, "tool_trade_date_hist_sina", return_value=_DF()),
        patch.object(ac, "_throttle", lambda: None),
    ):
        cal = ac.fetch_trade_calendar()
    assert cal == [date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 9)]
