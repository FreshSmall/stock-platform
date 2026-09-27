"""HiThink (同花顺官方) Financial-API client — the last-resort fallback source.

Upstream: https://fuyao.aicubes.cn — the official, free 同花顺 A-share data
service (https://github.com/HiThink-Tech/Financial-API). Appended AFTER
tencent/eastmoney in every :mod:`app.data.akshare_client` fallback chain;
without ``HITHINK_API_KEY`` configured every fetcher here returns ``[]``
immediately, so the chains behave exactly as before.

Row schema is identical to akshare_client's canonical contract: plain dicts
with english keys, ``volume`` in 手, ``amount`` in 元, ``trade_date`` a
``'YYYY-MM-DD'`` str.

REST contract notes (verified against the repo's docs/api/ 2026-09-27):
- envelope ``{code, message, request_id, data}``; HTTP is ALWAYS 200 —
  ``code == 0`` is the ONLY success signal.
- error classes: ``1xxx/2xxx/3xxx`` are caller-fixable (no retry);
  ``4001`` (rate-limit) and ``5xxx`` (server/upstream) retry with backoff.
- ``thscode`` format ``600519.SH``; historical K is ONE symbol per request
  with a ``[start, end]`` ms-timestamp window (≤ 10 years, Asia/Shanghai).
- volume is in 股 (shares) — normalized to 手 here (÷100), same as the
  eastmoney path in akshare_client.
- 端内专用 (NOT available via public REST — keep using eastmoney/tencent):
  minute K, capital flow, high-frequency, news events; no north-flow feed.
"""

import logging
import math
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone

import requests

# Import for the SIDE EFFECT as much as the settings: config neutralizes the
# machine's proxy env (same rationale as akshare_client) and carries the
# hithink_* knobs. fuyao.aicubes.cn is a domestic host — must not ride a
# local proxy that refuses it.
from app.core.config import settings

logger = logging.getLogger(__name__)

# Asia/Shanghai as a fixed +08:00 offset (CST has no DST); the API's whole
# date axis (date_ms values) is SH-midnight milliseconds.
_CST = timezone(timedelta(hours=8))

_http = requests.Session()
_http.trust_env = False
_http.headers.update({"User-Agent": "Mozilla/5.0"})

# ---------------------------------------------------------------------------
# Pacing, retry & auth-cooldown. The service sets no cumulative call cap but
# asks callers to avoid bursts; 4001 answers get exponential backoff, and a
# rejected key (2001/2003) parks the whole client for 10 minutes so a bad
# .env can't turn the fallback chain into a synchronous error loop.
# ---------------------------------------------------------------------------

_MIN_INTERVAL_SEC = 0.3
_last_ts = 0.0
_pace_lock = threading.Lock()

_RETRYABLE_CODES = {4001, 5001, 5002, 5003}
_AUTH_COOLDOWN_SEC = 600.0
_auth_cooldown_until = 0.0


def _pace() -> None:
    global _last_ts
    with _pace_lock:
        wait = _MIN_INTERVAL_SEC - (time.time() - _last_ts)
        if wait > 0:
            time.sleep(wait)
        _last_ts = time.time()


def is_enabled() -> bool:
    """True when a HiThink API key is configured (source participates)."""
    return bool(settings.hithink_api_key)


def _get(path: str, params: dict | None = None, timeout: float = 15.0):
    """GET one HiThink endpoint and unwrap the envelope.

    :return: the ``data`` payload on ``code == 0``; ``None`` on business
        error, auth rejection (client cools down), or exhausted retries.
        ``None`` is the caller's cue to fall through — it does NOT mean the
        symbol genuinely has no data.
    """
    global _auth_cooldown_until
    if not settings.hithink_api_key:
        return None
    if time.time() < _auth_cooldown_until:
        return None
    url = settings.hithink_base_url.rstrip("/") + path
    last_err: Exception | None = None
    for attempt in range(3):
        _pace()
        try:
            r = _http.get(
                url,
                params=params,
                headers={"X-api-key": settings.hithink_api_key},
                timeout=timeout,
            )
            if r.status_code == 429:
                # Contract: a 429 means rate-limited regardless of whether the
                # body carries the standard envelope — treat exactly like a
                # code=4001 (backoff, bounded retry), not like a network error.
                last_err = RuntimeError("rate limited (HTTP 429)")
                time.sleep(2 ** (attempt + 1))
                continue
            r.raise_for_status()
            body = r.json()
        except Exception as e:  # noqa: BLE001 - network blip → backoff, retry
            last_err = e
            time.sleep(2 ** (attempt + 1))
            continue
        code = body.get("code")
        if code == 0:
            return body.get("data")
        if code in (2001, 2003):
            _auth_cooldown_until = time.time() + _AUTH_COOLDOWN_SEC
            logger.error(
                "hithink auth rejected (code=%s, %s) — cooling down %.0fs; "
                "check HITHINK_API_KEY",
                code, body.get("message"), _AUTH_COOLDOWN_SEC,
            )
            return None
        if code not in _RETRYABLE_CODES:
            logger.warning(
                "hithink %s business error code=%s: %s",
                path, code, body.get("message"),
            )
            return None
        last_err = RuntimeError(f"code={code}: {body.get('message')}")
        time.sleep(2 ** (attempt + 1))
    logger.warning("hithink %s failed after retries: %s", path, last_err)
    return None


# ---------------------------------------------------------------------------
# Shared coercers (self-contained on purpose — no akshare_client import, so
# the two client modules stay decoupled and lazy-importable in both ways).
# ---------------------------------------------------------------------------


def _to_float(v) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _shares_to_lots(v) -> int | None:
    """股 (HiThink volume unit) → 手, the canonical schema's unit."""
    shares = _to_float(v)
    if shares is None:
        return None
    return int(shares / 100)


def _to_thscode(code: str) -> str:
    """6-digit A-share code → thscode (``600519`` → ``600519.SH``).

    Two-digit prefixes first (accurate for every real listing incl. the BJ
    92xxxx series), first-digit fallback for anything unforeseen.
    """
    c = str(code).strip()
    if "." in c:  # already a thscode
        return c.upper()
    two = c[:2]
    if two in ("60", "68", "90"):
        suffix = "SH"
    elif two in ("43", "83", "87", "88", "92"):
        suffix = "BJ"
    elif two in ("00", "30", "20"):
        suffix = "SZ"
    else:
        suffix = {"6": "SH", "9": "SH", "5": "SH", "4": "BJ", "8": "BJ"}.get(
            c[:1], "SZ"
        )
    return f"{c}.{suffix}"


def _index_thscode(symbol: str) -> str | None:
    """``sh000001`` → ``000001.SH``; thscodes pass through (board indexes
    like ``886042.TI`` reach the same historical endpoint)."""
    s = str(symbol).strip()
    if "." in s:
        return s.upper()
    low = s.lower()
    if low.startswith("sh"):
        return f"{s[2:]}.SH"
    if low.startswith("sz"):
        return f"{s[2:]}.SZ"
    return None


def _ymd_to_ms(d: str) -> int:
    """``'YYYYMMDD'`` → Asia/Shanghai midnight milliseconds."""
    return int(
        datetime.strptime(d, "%Y%m%d").replace(tzinfo=_CST).timestamp() * 1000
    )


def _ms_to_ymd(ms) -> str | None:
    """SH-midnight ms → ``'YYYY-MM-DD'`` (the canonical trade_date shape)."""
    f = _to_float(ms)
    if f is None:
        return None
    return datetime.fromtimestamp(f / 1000, tz=_CST).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Trade calendar (fetch_trade_calendar fallback when sina is down).
# ---------------------------------------------------------------------------


def fetch_trade_calendar() -> list:
    """A-share trading dates via ``GET /api/a-share/calendar/trading-days``.

    ⚠ The endpoint serves a FIXED window of the past year only — no future
    dates. That is enough for startup gap detection (recent-day holes) but
    NOT a replacement for sina's calendar (which the scheduler uses to gate
    upcoming days) — hence this stays a fallback.

    :return: ``list[date]`` ascending. ``[]`` when disabled/failed. Raises
        nothing — the akshare_client wrapper decides whether to propagate.
    """
    if not settings.hithink_api_key:
        return []
    data = _get("/api/a-share/calendar/trading-days")
    out: list = []
    for it in (data or {}).get("item") or []:
        d = it.get("date")  # 'yyyyMMdd' str, Asia/Shanghai
        if not isinstance(d, str) or len(d) != 8 or not d.isdigit():
            continue
        try:
            out.append(date(int(d[:4]), int(d[4:6]), int(d[6:8])))
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------------------
# Daily K (all three adjust bases) — the core fallback.
# ---------------------------------------------------------------------------


def fetch_daily_quotes(
    symbol: str, start_date: str, end_date: str, adjust: str = ""
) -> list[dict]:
    """Fetch daily OHLCV for one A-share symbol from HiThink.

    ``GET /api/a-share/prices/historical`` — one symbol per request, ms
    window ≤ 10 years (callers already chunk longer ranges). ``adjust`` is
    one of ``""`` (raw) / ``"qfq"`` / ``"hfq"`` → API ``none``/``forward``/
    ``backward``.

    :param symbol: 6-digit code, e.g. ``'600519'`` (thscode also accepted).
    :param start_date: ``'YYYYMMDD'`` (inclusive).
    :param end_date: ``'YYYYMMDD'`` (inclusive — the request sends end+1day
        midnight because the API treats the ms bound as an instant).
    :return: rows with keys ``stock_code, trade_date, open, close, high,
        low, volume(手), amount(元), pct_change, turnover`` plus
        ``_source='hithink'``. ``pct_change`` is derived from consecutive
        closes of the returned (adjusted) series — exact within a basis,
        ``None`` on the first bar. ``turnover`` (rate) is ``None``: the API
        does not expose it. ``[]`` when disabled/failed/genuinely empty.
    """
    if not settings.hithink_api_key:
        return []
    adjust_api = {"": "none", "qfq": "forward", "hfq": "backward"}.get(adjust)
    if adjust_api is None:
        logger.warning("hithink: unknown adjust %r for %s", adjust, symbol)
        return []
    try:
        datetime.strptime(start_date, "%Y%m%d")
        end_dt = datetime.strptime(end_date, "%Y%m%d")
    except ValueError:
        return []
    end_ms = int(
        (end_dt + timedelta(days=1)).replace(tzinfo=_CST).timestamp() * 1000
    )
    data = _get(
        "/api/a-share/prices/historical",
        params={
            "thscode": _to_thscode(symbol),
            "interval": "1d",
            "start": _ymd_to_ms(start_date),
            "end": end_ms,
            "adjust": adjust_api,
        },
    )
    if not data:
        return []
    out: list[dict] = []
    prev_close: float | None = None
    for it in data.get("item") or []:
        d_str = _ms_to_ymd(it.get("date_ms"))
        if d_str is None:
            continue
        compact = d_str.replace("-", "")
        if compact < start_date or compact > end_date:
            continue
        close = _to_float(it.get("close_price"))
        pct = None
        if prev_close and close:
            pct = round((close - prev_close) / prev_close * 100, 4)
        out.append(
            {
                "stock_code": symbol,
                "trade_date": d_str,
                "open": _to_float(it.get("open_price")),
                "close": close,
                "high": _to_float(it.get("high_price")),
                "low": _to_float(it.get("low_price")),
                "volume": _shares_to_lots(it.get("volume")),
                "amount": _to_float(it.get("turnover")),
                "pct_change": pct,
                "turnover": None,  # API does not expose turnover rate
                "_source": "hithink",
            }
        )
        prev_close = close
    return out


# ---------------------------------------------------------------------------
# Whole-market spot table (stock_pool universe fallback).
# ---------------------------------------------------------------------------

_SPOT_PAGE_LIMIT = 500
_SPOT_MAX_PAGES = 80  # 500×80 = 40k rows — safety cap, market is ~5.4k


def fetch_spot_table() -> list[dict]:
    """Whole-market spot snapshot — paged HiThink snapshot + name lookup.

    ``GET /api/a-share/prices/snapshot`` (limit/offset pages) for quotes and
    one ``GET /api/meta/tickers/list?asset_type=a-share`` call for the
    code→name map (the snapshot endpoint doesn't return names).

    :return: rows with the ``stock_pool`` canonical keys ``stock_code,
        stock_name, close, pct_change, turnover`` — valuation fields
        (``pe/pb/total_mv/circ_mv``) are ``None`` on this source, same
        contract as the Tencent rank fallback. ``[]`` on failure.
    """
    if not settings.hithink_api_key:
        return []
    names = _a_share_names()
    out: list[dict] = []
    offset = 0
    for _ in range(_SPOT_MAX_PAGES):
        data = _get(
            "/api/a-share/prices/snapshot",
            params={"limit": _SPOT_PAGE_LIMIT, "offset": offset},
        )
        items = (data or {}).get("item") or []
        if not items:
            break
        for it in items:
            code = str(it.get("ticker") or "")
            if not re.fullmatch(r"\d{6}", code):
                continue
            out.append(
                {
                    "stock_code": code,
                    "stock_name": names.get(code),
                    "close": _to_float(it.get("last_price")),
                    "pct_change": _to_float(it.get("price_change_ratio_pct")),
                    "turnover": None,
                    "pe": None,
                    "pb": None,
                    "total_mv": None,
                    "circ_mv": None,
                }
            )
        if len(items) < _SPOT_PAGE_LIMIT:
            break
        offset += _SPOT_PAGE_LIMIT
    if out:
        logger.info("hithink spot table: %d stocks (paged)", len(out))
    return out


def _a_share_names() -> dict[str, str]:
    """``{stock_code: name}`` for the whole A-share list (one request)."""
    data = _get(
        "/api/meta/tickers/list",
        params={"asset_type": "a-share", "limit": 10000, "offset": 0},
    )
    names: dict[str, str] = {}
    for it in (data or {}).get("item") or []:
        ticker = str(it.get("ticker") or "")
        name = it.get("name")
        if re.fullmatch(r"\d{6}", ticker) and isinstance(name, str):
            names[ticker] = name
    return names


# ---------------------------------------------------------------------------
# Index daily K (index_sync fallback).
# ---------------------------------------------------------------------------

# ~1200 calendar days ≈ 800 trading bars — the same depth the Tencent index
# path serves; well inside the API's 10-year window cap.
_INDEX_WINDOW_DAYS = 1200


def fetch_index_quotes(symbol: str, index_name: str = "") -> list[dict]:
    """Fetch daily index history from HiThink.

    ``GET /api/a-share-index/prices/historical`` — also serves 同花顺板块
    indexes (``886042.TI``) passed through as-is. Indices carry no adjust
    semantics upstream.

    :param symbol: exchange-prefixed code, e.g. ``'sh000001'``; a full
        thscode is accepted unchanged.
    :return: rows with keys ``index_code (the input symbol), index_name,
        trade_date, open, close, high, low, amount(元), pct_change`` — the
        same shape :func:`akshare_client.fetch_index_quotes` returns.
        ``pct_change`` derived from consecutive closes. ``[]`` on failure.
    """
    if not settings.hithink_api_key:
        return []
    thscode = _index_thscode(symbol)
    if not thscode:
        return []
    end_dt = datetime.now(_CST) + timedelta(days=1)
    data = _get(
        "/api/a-share-index/prices/historical",
        params={
            "thscode": thscode,
            "interval": "1d",
            "start": int(
                (end_dt - timedelta(days=_INDEX_WINDOW_DAYS)).timestamp() * 1000
            ),
            "end": int(end_dt.timestamp() * 1000),
        },
    )
    if not data:
        return []
    out: list[dict] = []
    prev_close: float | None = None
    for it in data.get("item") or []:
        d_str = _ms_to_ymd(it.get("date_ms"))
        if d_str is None:
            continue
        close = _to_float(it.get("close_price"))
        pct = None
        if prev_close and close:
            pct = round((close - prev_close) / prev_close * 100, 4)
        out.append(
            {
                "index_code": symbol,
                "index_name": index_name,
                "trade_date": d_str,
                "open": _to_float(it.get("open_price")),
                "close": close,
                "high": _to_float(it.get("high_price")),
                "low": _to_float(it.get("low_price")),
                "amount": _to_float(it.get("turnover")),
                "pct_change": pct,
            }
        )
        prev_close = close
    return out


# ---------------------------------------------------------------------------
# Financial indicators (finance_sync fallback).
# ---------------------------------------------------------------------------

# report-quarter end dates keyed by quarter number (report 'yyyy-N').
_REPORT_QTR_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}

# HiThink index_id → canonical sa_financial_extra field. Verified live
# 2026-09-27: the growth ratios are served with a ``calculate_`` prefix and
# the net-profit one is the 归母 (parent-holder) basis — the docs' indicator
# table is stale on these, so both the live and documented forms are mapped.
# eps is NOT exposed by HiThink's indicator set — stays None on this source.
_INDICATOR_MAP = {
    "calculate_operating_income_yoy_growth_ratio": "revenue_growth",
    "operating_income_yoy_growth_ratio": "revenue_growth",
    "calculate_parent_holder_net_profit_yoy_growth_ratio": "profit_growth",
    "net_profit_yoy_growth_ratio": "profit_growth",
    "index_weighted_avg_roe": "roe",
}


def _recent_report_periods(n: int = 4, today: date | None = None) -> list[tuple]:
    """The latest ``n`` ended report periods, newest first.

    :return: list of ``(year, quarter)`` string tuples, e.g.
        ``[('2026', '2'), ('2026', '1'), ...]`` for 2026-09-27.
    """
    today = today or datetime.now(_CST).date()
    periods: list[tuple[str, str]] = []
    for year in range(today.year - 3, today.year + 1):
        for q in range(1, 5):
            m, d = _REPORT_QTR_END[q]
            if date(year, m, d) <= today:
                periods.append((str(year), str(q)))
    return list(reversed(periods[-n:]))


def fetch_financial_abstract(symbol: str) -> list[dict]:
    """Fetch per-report financial indicators for one stock from HiThink.

    ``GET /api/a-share/financials/indicators`` per report period — 4 requests
    for the latest 4 periods (only reached when the akshare primary already
    failed for this stock).

    :return: rows with keys ``stock_code, report_date, roe,
        revenue_growth, profit_growth`` (``eps`` is ``None`` — not exposed
        by HiThink). ``report_date`` is the quarter-end date, matching the
        akshare primary's convention. Ascending by report_date, ``[]`` on
        failure.
    """
    if not settings.hithink_api_key:
        return []
    thscode = _to_thscode(symbol)
    out: list[dict] = []
    for year, q in _recent_report_periods():
        data = _get(
            "/api/a-share/financials/indicators",
            params={"thscode": thscode, "report": f"{year}-{q}"},
        )
        if not data:
            continue
        vals: dict[str, float | None] = {}
        for ability in data.get("abilities") or []:
            for ind in ability.get("indicators") or []:
                field = _INDICATOR_MAP.get(ind.get("index_id"))
                if field and field not in vals:
                    vals[field] = _to_float(ind.get("value"))
        if not vals:
            continue
        m, d = _REPORT_QTR_END[int(q)]
        out.append(
            {
                "stock_code": symbol,
                "report_date": f"{year}-{m:02d}-{d:02d}",
                "roe": vals.get("roe"),
                "eps": None,
                "revenue_growth": vals.get("revenue_growth"),
                "profit_growth": vals.get("profit_growth"),
            }
        )
    out.sort(key=lambda r: r["report_date"])
    return out


# ---------------------------------------------------------------------------
# Valuation snapshot — standalone utility (admin probe / future enrichment).
# ---------------------------------------------------------------------------

_VALUATION_BATCH = 100  # server cap per request


def fetch_valuations(thscodes: list[str]) -> list[dict]:
    """Batch PE/PB/PS/PCF snapshot (``GET /api/a-share/valuations/snapshot``).

    :param thscodes: 6-digit codes or full thscodes; batched 100/request.
    :return: rows ``{stock_code, name, pe_ttm, pe_mrq, pb_mrq, ps_ttm,
        pcf_ttm}`` in input order. ``[]`` when disabled/failed.
    """
    if not settings.hithink_api_key or not thscodes:
        return []
    codes = [_to_thscode(c) for c in thscodes]
    out: list[dict] = []
    for i in range(0, len(codes), _VALUATION_BATCH):
        data = _get(
            "/api/a-share/valuations/snapshot",
            params={"thscodes": ",".join(codes[i:i + _VALUATION_BATCH])},
        )
        for it in (data or {}).get("item") or []:
            code = str(it.get("ticker") or "")
            if not code:
                code = str(it.get("thscode") or "").split(".")[0]
            out.append(
                {
                    "stock_code": code,
                    "name": it.get("name"),
                    "pe_ttm": _to_float(it.get("pe_ttm")),
                    "pe_mrq": _to_float(it.get("pe_mrq")),
                    "pb_mrq": _to_float(it.get("pb_mrq")),
                    "ps_ttm": _to_float(it.get("ps_ttm")),
                    "pcf_ttm": _to_float(it.get("pcf_ttm")),
                }
            )
    return out
