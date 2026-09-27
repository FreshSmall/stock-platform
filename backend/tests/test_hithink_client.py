"""Unit tests for the HiThink (同花顺官方) fallback data source.

All tests are offline: the HTTP choke point (:func:`hithink_client._get`
and the shared session behind it) is monkeypatched, never the network.

Pinned behaviors:
- canonical row schema parity with akshare_client (volume in 手 = 股÷100,
  ``trade_date`` 'YYYY-MM-DD', ``_source='hithink'``)
- envelope handling: ``code==0`` only success; 1xxx/2xxx/3xxx no-retry,
  4001/5xxx retried, auth rejection parks the client in cooldown
- disabled-by-default: without HITHINK_API_KEY every fetcher returns []
  without a single request
- fallback wiring: akshare_client's chains fall through to hithink only
  after tencent AND eastmoney came up empty
"""

import pytest
import requests

from app.data import akshare_client, hithink_client

# 2024-05-20 00:00 Asia/Shanghai — from the official prices/historical doc
# example (1716134400000). Anchors the ms↔date conversions to reality.
_DOC_EXAMPLE_MS = 1716134400000
_DOC_EXAMPLE_DATE = "2024-05-20"


@pytest.fixture(autouse=True)
def _quiet_state(monkeypatch):
    """Fast + deterministic: no pacing sleeps, no leftover auth cooldown."""
    monkeypatch.setattr(hithink_client, "_auth_cooldown_until", 0.0)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    yield


def _enable(monkeypatch, key="test-key"):
    monkeypatch.setattr(hithink_client.settings, "hithink_api_key", key)


def _kline_item(ms, o, hi, lo, c, volume, turnover):
    return {
        "date_ms": ms,
        "open_price": o,
        "high_price": hi,
        "low_price": lo,
        "close_price": c,
        "volume": volume,
        "turnover": turnover,
    }


# ---------------------------------------------------------------------------
# Disabled-by-default
# ---------------------------------------------------------------------------


def test_disabled_without_key_makes_no_requests(monkeypatch):
    """No HITHINK_API_KEY → every fetcher returns [] without touching _get."""
    _enable(monkeypatch, key="")

    def _boom(*a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("_get must not be called when disabled")

    monkeypatch.setattr(hithink_client, "_get", _boom)
    assert hithink_client.fetch_daily_quotes("600519", "20260901", "20260930") == []
    assert hithink_client.fetch_spot_table() == []
    assert hithink_client.fetch_index_quotes("sh000001") == []
    assert hithink_client.fetch_financial_abstract("600519") == []
    assert hithink_client.fetch_valuations(["600519"]) == []
    assert hithink_client.is_enabled() is False


def test_enabled_flag_follows_key(monkeypatch):
    _enable(monkeypatch)
    assert hithink_client.is_enabled() is True


# ---------------------------------------------------------------------------
# _get: envelope / retry / auth cooldown
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, body=None, status=200):
        self._body = body
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")

    def json(self):
        return self._body


class _FakeSession:
    """Scripted responses for _get; records every call for assertions."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(
            {"url": url, "params": params, "headers": headers, "timeout": timeout}
        )
        item = self.responses.pop(0)
        return item() if callable(item) else item


def _use_session(monkeypatch, responses):
    fake = _FakeSession(responses)
    monkeypatch.setattr(hithink_client, "_http", fake)
    _enable(monkeypatch)
    return fake


def test_get_success_unwraps_data(monkeypatch):
    fake = _use_session(
        monkeypatch, [_FakeResp(body={"code": 0, "message": "success", "data": {"x": 1}})]
    )
    assert hithink_client._get("/api/ping") == {"x": 1}
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == "https://fuyao.aicubes.cn/api/ping"
    assert call["headers"]["X-api-key"] == "test-key"


def test_get_caller_error_no_retry(monkeypatch):
    """1xxx is caller-fixable: one request, no retry, None returned."""
    fake = _use_session(
        monkeypatch,
        [_FakeResp(body={"code": 1002, "message": "bad param", "data": None})],
    )
    assert hithink_client._get("/api/a-share/prices/historical") is None
    assert len(fake.calls) == 1


def test_get_rate_limited_retries_then_gives_up(monkeypatch):
    """4001 is retryable: 3 attempts total, then None."""
    fake = _use_session(
        monkeypatch,
        [_FakeResp(body={"code": 4001, "message": "rate limited", "data": None})] * 3,
    )
    assert hithink_client._get("/api/ping") is None
    assert len(fake.calls) == 3


def test_get_http_429_treated_as_rate_limit(monkeypatch):
    """Contract: HTTP 429 (non-envelope body allowed) == code 4001 — backoff,
    bounded retry, recovery when the limit lifts."""
    fake = _use_session(
        monkeypatch,
        [
            _FakeResp(body={"raw": "Too Many Requests"}, status=429),
            _FakeResp(body={"code": 0, "message": "success", "data": {"ok": True}}),
        ],
    )
    assert hithink_client._get("/api/ping") == {"ok": True}
    assert len(fake.calls) == 2


def test_get_rate_limited_then_succeeds(monkeypatch):
    fake = _use_session(
        monkeypatch,
        [
            _FakeResp(body={"code": 4001, "message": "rate limited", "data": None}),
            _FakeResp(body={"code": 0, "message": "success", "data": {"ok": True}}),
        ],
    )
    assert hithink_client._get("/api/ping") == {"ok": True}
    assert len(fake.calls) == 2


def test_get_network_error_retries(monkeypatch):
    def _neterr():
        raise requests.ConnectionError("refused")

    fake = _use_session(
        monkeypatch,
        [
            _neterr,
            _FakeResp(body={"code": 0, "message": "success", "data": {"ok": 1}}),
        ],
    )
    assert hithink_client._get("/api/ping") == {"ok": 1}
    assert len(fake.calls) == 2


def test_get_auth_rejection_sets_cooldown(monkeypatch):
    """2001/2003 park the client: later _get returns None with ZERO requests."""
    fake = _use_session(
        monkeypatch,
        [_FakeResp(body={"code": 2003, "message": "invalid key", "data": None})],
    )
    assert hithink_client._get("/api/ping") is None
    assert len(fake.calls) == 1
    # Second call short-circuits on the cooldown, no HTTP at all.
    assert hithink_client._get("/api/ping") is None
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def test_ms_to_ymd_doc_example():
    assert hithink_client._ms_to_ymd(_DOC_EXAMPLE_MS) == _DOC_EXAMPLE_DATE
    assert hithink_client._ms_to_ymd(None) is None


def test_ymd_to_ms_roundtrip():
    ms = hithink_client._ymd_to_ms("20240520")
    assert ms == _DOC_EXAMPLE_MS


# ---------------------------------------------------------------------------
# thscode mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "thscode"),
    [
        ("600519", "600519.SH"),  # SH main board
        ("688981", "688981.SH"),  # STAR
        ("900901", "900901.SH"),  # SH B-share
        ("000001", "000001.SZ"),  # SZ main
        ("002415", "002415.SZ"),  # SZ SME (now main)
        ("300750", "300750.SZ"),  # ChiNext
        ("430047", "430047.BJ"),  # BJ (old NEEQ)
        ("832566", "832566.BJ"),  # BJ
        ("920002", "920002.BJ"),  # BJ new 92xxxx series
        ("600519.SH", "600519.SH"),  # thscode passthrough
    ],
)
def test_to_thscode(code, thscode):
    assert hithink_client._to_thscode(code) == thscode


@pytest.mark.parametrize(
    ("symbol", "thscode"),
    [
        ("sh000001", "000001.SH"),
        ("sz399001", "399001.SZ"),
        ("886042.TI", "886042.TI"),  # board index passthrough
        ("123456", None),  # un-mappable
    ],
)
def test_index_thscode(symbol, thscode):
    assert hithink_client._index_thscode(symbol) == thscode


# ---------------------------------------------------------------------------
# fetch_daily_quotes
# ---------------------------------------------------------------------------


def test_daily_quotes_row_mapping_and_units(monkeypatch):
    _enable(monkeypatch)
    ms1 = hithink_client._ymd_to_ms("20260924")
    ms2 = hithink_client._ymd_to_ms("20260925")
    captured = {}

    def fake_get(path, params=None, timeout=15.0):
        captured.update(params or {})
        return {
            "item": [
                _kline_item(ms1, 9.8, 10.2, 9.7, 10.0, 3098875, 3937375200.0),
                _kline_item(ms2, 10.1, 10.6, 10.0, 10.5, 2500000, 2600000000.0),
            ]
        }

    monkeypatch.setattr(hithink_client, "_get", fake_get)
    rows = hithink_client.fetch_daily_quotes(
        "600519", "20260901", "20260930", "qfq"
    )
    assert len(rows) == 2
    r1, r2 = rows
    # request params: thscode + forward adjust + inclusive end bound (+1 day)
    assert captured["thscode"] == "600519.SH"
    assert captured["adjust"] == "forward"
    assert captured["interval"] == "1d"
    assert captured["start"] == hithink_client._ymd_to_ms("20260901")
    assert captured["end"] == hithink_client._ymd_to_ms("20261001")
    # canonical row shape
    assert r1["stock_code"] == "600519"
    assert r1["trade_date"] == "2026-09-24"
    assert r1["open"] == 9.8 and r1["high"] == 10.2 and r1["low"] == 9.7
    assert r1["close"] == 10.0
    assert r1["volume"] == 30988  # 股 → 手 (÷100, floored)
    assert r1["amount"] == 3937375200.0
    assert r1["pct_change"] is None  # first bar of the window
    assert r1["turnover"] is None
    assert r1["_source"] == "hithink"
    # pct derived from consecutive closes: (10.5-10.0)/10.0 = +5%
    assert r2["pct_change"] == 5.0
    assert r2["trade_date"] == "2026-09-25"


def test_daily_quotes_window_filters_out_of_range(monkeypatch):
    _enable(monkeypatch)

    def fake_get(path, params=None, timeout=15.0):
        return {
            "item": [
                _kline_item(hithink_client._ymd_to_ms("20260831"), 9, 9, 9, 9, 100, 1),
                _kline_item(hithink_client._ymd_to_ms("20260915"), 10, 10, 10, 10, 100, 1),
                _kline_item(hithink_client._ymd_to_ms("20261008"), 11, 11, 11, 11, 100, 1),
            ]
        }

    monkeypatch.setattr(hithink_client, "_get", fake_get)
    rows = hithink_client.fetch_daily_quotes("600519", "20260901", "20260930")
    assert [r["trade_date"] for r in rows] == ["2026-09-15"]


def test_daily_quotes_adjust_basis_mapping(monkeypatch):
    _enable(monkeypatch)
    seen = []

    def fake_get(path, params=None, timeout=15.0):
        seen.append(params["adjust"])
        return {"item": []}

    monkeypatch.setattr(hithink_client, "_get", fake_get)
    hithink_client.fetch_daily_quotes("600519", "20260901", "20260930", "")
    hithink_client.fetch_daily_quotes("600519", "20260901", "20260930", "hfq")
    assert seen == ["none", "backward"]
    # unknown basis → rejected before any request
    assert hithink_client.fetch_daily_quotes("600519", "20260901", "20260930", "xfq") == []
    assert len(seen) == 2


def test_daily_quotes_business_error_yields_empty(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(hithink_client, "_get", lambda *a, **k: None)
    assert hithink_client.fetch_daily_quotes("600519", "20260901", "20260930") == []


# ---------------------------------------------------------------------------
# fetch_spot_table
# ---------------------------------------------------------------------------


def test_spot_table_pagination_and_names(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(hithink_client, "_SPOT_PAGE_LIMIT", 2)  # tiny pages
    calls = []

    names_payload = {
        "item": [
            {"thscode": "600519.SH", "ticker": "600519", "name": "贵州茅台"},
            {"thscode": "000001.SZ", "ticker": "000001", "name": "平安银行"},
        ]
    }
    page1 = {
        "total": 3,
        "item": [
            {"thscode": "600519.SH", "ticker": "600519",
             "last_price": 1277.8, "price_change_ratio_pct": 1.735669,
             "open_price": 1252.08, "volume": 3098875, "turnover": 3937375200},
            {"thscode": "000001.SZ", "ticker": "000001",
             "last_price": 11.2, "price_change_ratio_pct": -0.5, "volume": 8e7,
             "turnover": 9e8},
        ],
    }
    page2 = {  # partial page → pagination stops
        "total": 3,
        "item": [
            {"thscode": "832566.BJ", "ticker": "832566",
             "last_price": 20.0, "price_change_ratio_pct": 0.0, "volume": 1e5,
             "turnover": 2e6},
        ],
    }

    def fake_get(path, params=None, timeout=15.0):
        calls.append((path, dict(params or {})))
        if path.endswith("/meta/tickers/list"):
            return names_payload
        if params.get("offset") == 0:
            return page1
        return page2

    monkeypatch.setattr(hithink_client, "_get", fake_get)
    rows = hithink_client.fetch_spot_table()
    assert [r["stock_code"] for r in rows] == ["600519", "000001", "832566"]
    r1 = rows[0]
    assert r1["stock_name"] == "贵州茅台"
    assert r1["close"] == 1277.8
    assert r1["pct_change"] == 1.735669
    # valuation fields are None on this source (Tencent-fallback parity)
    assert r1["turnover"] is None and r1["pe"] is None and r1["pb"] is None
    assert r1["total_mv"] is None and r1["circ_mv"] is None
    # page-2-only stock has no name entry → None
    assert rows[2]["stock_name"] is None
    # pagination: names call + offset 0 + offset 2
    assert calls[0][0].endswith("/meta/tickers/list")
    assert calls[1] == ("/api/a-share/prices/snapshot", {"limit": 2, "offset": 0})
    assert calls[2] == ("/api/a-share/prices/snapshot", {"limit": 2, "offset": 2})


# ---------------------------------------------------------------------------
# fetch_index_quotes
# ---------------------------------------------------------------------------


def test_index_quotes_mapping(monkeypatch):
    _enable(monkeypatch)
    captured = {}

    def fake_get(path, params=None, timeout=15.0):
        captured.update(params or {})
        return {
            "item": [
                _kline_item(hithink_client._ymd_to_ms("20260924"),
                            3350.0, 3400.0, 3340.0, 3388.06, 3.21e8, 4.2e11),
                _kline_item(hithink_client._ymd_to_ms("20260925"),
                            3390.0, 3410.0, 3380.0, 3400.5, 3.0e8, 4.0e11),
            ]
        }

    monkeypatch.setattr(hithink_client, "_get", fake_get)
    rows = hithink_client.fetch_index_quotes("sh000001", "上证指数")
    assert captured["thscode"] == "000001.SH"
    assert "adjust" not in captured  # indices carry no adjust semantics
    assert len(rows) == 2
    r1, r2 = rows
    assert r1["index_code"] == "sh000001"  # caller's symbol preserved
    assert r1["index_name"] == "上证指数"
    assert r1["trade_date"] == "2026-09-24"
    assert r1["amount"] == 4.2e11
    assert r1["pct_change"] is None
    assert r2["pct_change"] == round((3400.5 - 3388.06) / 3388.06 * 100, 4)


def test_index_quotes_unmappable_symbol(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        hithink_client, "_get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch")),
    )
    assert hithink_client.fetch_index_quotes("zz123456") == []


# ---------------------------------------------------------------------------
# fetch_financial_abstract
# ---------------------------------------------------------------------------


def test_recent_report_periods_injected_today():
    from datetime import date

    periods = hithink_client._recent_report_periods(
        n=4, today=date(2026, 9, 27)
    )
    assert periods == [("2026", "2"), ("2026", "1"), ("2025", "4"), ("2025", "3")]
    # boundary: on Dec 31 the annual report ('4') just became "ended"
    periods = hithink_client._recent_report_periods(
        n=1, today=date(2026, 12, 31)
    )
    assert periods == [("2026", "4")]


def test_financial_abstract_indicator_mapping(monkeypatch):
    """Live-verified index_ids (2026-09-27): growth ratios carry the
    ``calculate_`` prefix; the documented unprefixed forms stay supported."""
    _enable(monkeypatch)
    monkeypatch.setattr(
        hithink_client, "_recent_report_periods",
        lambda n=4, today=None: [("2026", "2"), ("2026", "1")],
    )
    asked = []

    def fake_get(path, params=None, timeout=15.0):
        asked.append(params["report"])
        if params["report"] == "2026-2":
            return {
                "thscode": params["thscode"],
                "report": "2026-2",
                "abilities": [
                    {"ability": "profitability", "indicators": [
                        {"index_id": "index_weighted_avg_roe", "value": "12.34"},
                        {"index_id": "sale_gross_margin", "value": "91.0"},
                    ]},
                    {"ability": "growth", "indicators": [
                        {"index_id": "calculate_operating_income_yoy_growth_ratio",
                         "value": "-16.0031"},
                        {"index_id": "calculate_parent_holder_net_profit_yoy_growth_ratio",
                         "value": "3.5"},
                        {"index_id": "total_assets_growth_ratio", "value": "9.9"},
                    ]},
                ],
            }
        # 2026-1 answered with the DOCUMENTED (unprefixed) ids — also mapped.
        if params["report"] == "2026-1":
            return {
                "thscode": params["thscode"],
                "report": "2026-1",
                "abilities": [
                    {"ability": "growth", "indicators": [
                        {"index_id": "operating_income_yoy_growth_ratio",
                         "value": "7.7"},
                        {"index_id": "net_profit_yoy_growth_ratio", "value": "8.8"},
                    ]},
                ],
            }
        return None  # any other period failed upstream → skipped

    monkeypatch.setattr(hithink_client, "_get", fake_get)
    rows = hithink_client.fetch_financial_abstract("300033")
    assert asked == ["2026-2", "2026-1"]
    assert len(rows) == 2
    r = rows[0]  # 2026-1 (documented unprefixed ids) sorts first
    assert r["stock_code"] == "300033"
    assert r["report_date"] == "2026-03-31"  # quarter-end convention, ascending
    assert r["roe"] is None  # that period's payload carries no roe block
    assert r["revenue_growth"] == 7.7  # from the documented-id period
    assert r["profit_growth"] == 8.8
    assert r["eps"] is None  # not exposed by HiThink
    assert rows[1]["report_date"] == "2026-06-30"
    assert rows[1]["roe"] == 12.34
    assert rows[1]["revenue_growth"] == -16.0031  # live calculate_ ids
    assert rows[1]["profit_growth"] == 3.5
    # unmapped indicators (gross margin, assets growth) never leak in
    assert set(r) == {"stock_code", "report_date", "roe", "eps",
                      "revenue_growth", "profit_growth"}


def test_financial_abstract_all_periods_empty(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        hithink_client, "_recent_report_periods",
        lambda n=4, today=None: [("2026", "2")],
    )
    monkeypatch.setattr(hithink_client, "_get", lambda *a, **k: None)
    assert hithink_client.fetch_financial_abstract("300033") == []


# ---------------------------------------------------------------------------
# fetch_valuations
# ---------------------------------------------------------------------------


def test_valuations_batches_by_100(monkeypatch):
    _enable(monkeypatch)
    batches = []

    def fake_get(path, params=None, timeout=15.0):
        batches.append(params["thscodes"].split(","))
        return {
            "item": [
                {"thscode": c, "ticker": c.split(".")[0], "name": f"N{c[:6]}",
                 "pe_ttm": 21.35, "pe_mrq": 20.88, "pb_mrq": 7.15,
                 "ps_ttm": 10.32, "pcf_ttm": 19.77}
                for c in params["thscodes"].split(",")
            ]
        }

    monkeypatch.setattr(hithink_client, "_get", fake_get)
    codes = [f"{600000 + i}" for i in range(150)]
    rows = hithink_client.fetch_valuations(codes)
    assert len(batches) == 2
    assert all(len(b) <= 100 for b in batches)
    assert len(rows) == 150
    assert rows[0]["stock_code"] == "600000"
    assert rows[0]["pe_ttm"] == 21.35 and rows[0]["pb_mrq"] == 7.15


def test_valuations_empty_input(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        hithink_client, "_get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch")),
    )
    assert hithink_client.fetch_valuations([]) == []


# ---------------------------------------------------------------------------
# Trade calendar (sina fallback chain)
# ---------------------------------------------------------------------------


def test_trade_calendar_parses_yyyymmdd(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        hithink_client, "_get",
        lambda *a, **k: {"item": [
            {"date_ms": 1747929600000, "date": "20260522"},
            {"date_ms": 1748188800000, "date": "20260525"},
            {"date_ms": None, "date": "garbage"},   # skipped
            {"date_ms": 0, "date": "2026 5 26"},    # skipped (not compact)
        ]},
    )
    from datetime import date

    assert hithink_client.fetch_trade_calendar() == [
        date(2026, 5, 22), date(2026, 5, 25),
    ]


def test_trade_calendar_disabled(monkeypatch):
    _enable(monkeypatch, key="")
    assert hithink_client.fetch_trade_calendar() == []


def test_trade_calendar_chain_sina_down(monkeypatch):
    """akshare_client.fetch_trade_calendar: sina raises → hithink serves."""
    _enable(monkeypatch)
    monkeypatch.setattr(akshare_client, "_throttle", lambda: None)

    def _sina_boom(**kwargs):
        raise RuntimeError("sina down")

    monkeypatch.setattr(akshare_client.ak, "tool_trade_date_hist_sina", _sina_boom)
    from datetime import date

    monkeypatch.setattr(
        hithink_client, "fetch_trade_calendar",
        lambda: [date(2026, 9, 24)],
    )
    assert akshare_client.fetch_trade_calendar() == [date(2026, 9, 24)]


def test_trade_calendar_chain_both_down_raises(monkeypatch):
    """Both sources down → propagate, callers fall back to weekday judgment."""
    _enable(monkeypatch)
    monkeypatch.setattr(akshare_client, "_throttle", lambda: None)

    def _sina_boom(**kwargs):
        raise RuntimeError("sina down")

    monkeypatch.setattr(akshare_client.ak, "tool_trade_date_hist_sina", _sina_boom)
    monkeypatch.setattr(hithink_client, "fetch_trade_calendar", lambda: [])
    with pytest.raises(RuntimeError):
        akshare_client.fetch_trade_calendar()


# ---------------------------------------------------------------------------
# Fallback wiring in akshare_client
# ---------------------------------------------------------------------------


def _starve_primary_sources(monkeypatch):
    monkeypatch.setattr(
        akshare_client, "_fetch_daily_quotes_tencent", lambda *a, **k: []
    )
    monkeypatch.setattr(akshare_client, "_fetch_daily_quotes_em", lambda *a, **k: [])
    monkeypatch.setattr(akshare_client, "_throttle", lambda: None)


_HITHINK_ROW = {
    "stock_code": "600519",
    "trade_date": "2026-09-24",
    "open": 9.8, "close": 10.0, "high": 10.2, "low": 9.7,
    "volume": 30988, "amount": 3937375200.0,
    "pct_change": None, "turnover": None,
    "_source": "hithink",
}


def test_qfq_chain_falls_through_to_hithink(monkeypatch):
    _starve_primary_sources(monkeypatch)
    _enable(monkeypatch)
    monkeypatch.setattr(
        hithink_client, "fetch_daily_quotes",
        lambda *a, **k: [dict(_HITHINK_ROW)],
    )
    rows = akshare_client.fetch_daily_quotes("600519", "20260901", "20260930")
    assert rows and rows[0]["_source"] == "hithink"


def test_raw_chain_falls_through_and_tags_source(monkeypatch):
    _starve_primary_sources(monkeypatch)
    _enable(monkeypatch)
    monkeypatch.setattr(
        hithink_client, "fetch_daily_quotes",
        lambda *a, **k: [dict(_HITHINK_ROW)],
    )
    rows = akshare_client.fetch_daily_quotes_raw("600519", "20260901", "20260930")
    assert rows and rows[0]["_source"] == "hithink"


def test_chain_untouched_when_hithink_unavailable(monkeypatch):
    """Disabled key (or empty hithink result) → chain still ends in []."""
    _starve_primary_sources(monkeypatch)
    _enable(monkeypatch, key="")  # real hithink_client path: [] fast
    assert akshare_client.fetch_daily_quotes("600519", "20260901", "20260930") == []
    assert akshare_client.fetch_daily_quotes_raw("600519", "20260901", "20260930") == []
    assert akshare_client.fetch_daily_quotes_hfq("600519", "20260901", "20260930") == []


def test_hfq_chain_falls_through_to_hithink(monkeypatch):
    _starve_primary_sources(monkeypatch)
    _enable(monkeypatch)
    monkeypatch.setattr(
        hithink_client, "fetch_daily_quotes",
        lambda *a, **k: [dict(_HITHINK_ROW)],
    )
    rows = akshare_client.fetch_daily_quotes_hfq("600519", "20260901", "20260930")
    assert rows and rows[0]["_source"] == "hithink"


def test_spot_table_chain_falls_through_to_hithink(monkeypatch):
    monkeypatch.setattr(
        akshare_client, "_fetch_spot_table_eastmoney", lambda: []
    )
    monkeypatch.setattr(akshare_client, "_fetch_spot_table_tencent", lambda: [])
    _enable(monkeypatch)
    spot_row = {
        "stock_code": "600519", "stock_name": "贵州茅台", "close": 1277.8,
        "pct_change": 1.73, "turnover": None, "pe": None, "pb": None,
        "total_mv": None, "circ_mv": None,
    }
    monkeypatch.setattr(
        hithink_client, "fetch_spot_table", lambda: [dict(spot_row)]
    )
    rows = akshare_client.fetch_spot_table()
    assert rows and rows[0]["stock_code"] == "600519"


def test_index_chain_falls_through_to_hithink(monkeypatch):
    monkeypatch.setattr(akshare_client, "_tencent_get", lambda *a, **k: None)
    _enable(monkeypatch)
    monkeypatch.setattr(
        hithink_client, "fetch_index_quotes",
        lambda symbol, name="": [
            {"index_code": symbol, "index_name": name, "trade_date": "2026-09-24",
             "open": 3350.0, "close": 3388.06, "high": 3400.0, "low": 3340.0,
             "amount": 4.2e11, "pct_change": None}
        ],
    )
    rows = akshare_client.fetch_index_quotes("sh000001", "上证指数")
    assert rows and rows[0]["index_code"] == "sh000001"


def test_financial_abstract_fallback_on_primary_exception(monkeypatch):
    """akshare raises → hithink rows serve; hithink empty → raise surfaces
    (sync_all's circuit breaker counts that per-code failure)."""
    monkeypatch.setattr(akshare_client, "_throttle", lambda: None)

    def _raise(**kwargs):
        raise RuntimeError("eastmoney down")

    monkeypatch.setattr(akshare_client.ak, "stock_financial_abstract", _raise)
    _enable(monkeypatch)

    fin_row = {
        "stock_code": "600519", "report_date": "2026-06-30",
        "roe": 12.34, "eps": None, "revenue_growth": -16.0,
        "profit_growth": 3.5,
    }
    monkeypatch.setattr(
        hithink_client, "fetch_financial_abstract",
        lambda symbol: [dict(fin_row)],
    )
    rows = akshare_client.fetch_financial_abstract("600519")
    assert rows and rows[0]["roe"] == 12.34

    # hithink also empty → the original exception must propagate
    monkeypatch.setattr(
        hithink_client, "fetch_financial_abstract", lambda symbol: []
    )
    with pytest.raises(RuntimeError):
        akshare_client.fetch_financial_abstract("600519")


def test_financial_abstract_fallback_on_primary_empty(monkeypatch):
    """Genuine empty primary frame → hithink gets its chance too."""
    import pandas as pd

    monkeypatch.setattr(akshare_client, "_throttle", lambda: None)
    monkeypatch.setattr(
        akshare_client.ak, "stock_financial_abstract", lambda symbol: pd.DataFrame()
    )
    _enable(monkeypatch)
    monkeypatch.setattr(
        hithink_client, "fetch_financial_abstract",
        lambda symbol: [
            {"stock_code": symbol, "report_date": "2026-06-30", "roe": 1.0,
             "eps": None, "revenue_growth": None, "profit_growth": None}
        ],
    )
    rows = akshare_client.fetch_financial_abstract("600519")
    assert len(rows) == 1 and rows[0]["roe"] == 1.0


# ---------------------------------------------------------------------------
# Admin catalog & probe
# ---------------------------------------------------------------------------


def test_datasources_catalog_contains_hithink():
    from app.services.admin_service import DATASOURCES

    names = {d["name"] for d in DATASOURCES}
    assert "hithink" in names
    entry = next(d for d in DATASOURCES if d["name"] == "hithink")
    assert entry["type"] == "http"
    assert "HITHINK_API_KEY" in entry["note"]


def test_probe_without_key_reports_disabled(monkeypatch):
    from app.services.admin_service import test_datasource

    monkeypatch.setattr(hithink_client.settings, "hithink_api_key", "")
    result = test_datasource("hithink")
    assert result["ok"] is False
    assert "HITHINK_API_KEY" in result["detail"]
