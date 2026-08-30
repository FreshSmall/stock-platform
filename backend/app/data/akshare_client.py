"""AkShare data-source client wrapper.

Thin boundary around akshare so the rest of the codebase can mock this module
instead of the network. Includes light rate-limiting (sleep-based throttle)
and retry (tenacity) so transient eastmoney blips don't abort a sync run.

Verified against akshare 1.18.80:
    ak.stock_zh_a_hist(symbol='600519', period='daily',
                       start_date='YYYYMMDD', end_date='YYYYMMDD',
                       adjust='qfq')
returns a DataFrame with columns:
    日期, 股票代码, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率
Note: ``日期`` is returned as a Python ``str`` ('YYYY-MM-DD'), NOT a Timestamp.
"""

import logging
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import date, datetime, time as dtime
from typing import Any

import akshare as ak
import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
)

# Import for the SIDE EFFECT: config neutralizes the machine's proxy config
# (env vars + ``no_proxy`` for the macOS system proxy) before any request is
# made. Without this, scripts that import only this module would let akshare's
# internal requests hit the local proxy, which refuses finance hosts.
from app.core import config  # noqa: F401

logger = logging.getLogger(__name__)

# Shared session for the app's OWN HTTP fetches (Tencent kline endpoints).
# ``trust_env=False`` keeps requests off the machine's proxy configuration —
# both env vars and (on macOS) the system proxy that urllib discovers via
# SystemConfiguration. Observed 2026-08-14/16: a local Clash proxy refused
# connections to the finance hosts mid-run, killing a whole daily-K sync.
# akshare-internal calls can't take a session, they rely on the ``no_proxy``
# env default set in app.core.config instead.
_http = requests.Session()
_http.trust_env = False
_http.headers.update({"User-Agent": "Mozilla/5.0"})

# ---------------------------------------------------------------------------
# Tencent-request pacing & WAF cooldown.
#
# Observed 2026-08-26..29: the classic ``web.ifzq.gtimg.cn`` WAF serves 501
# challenge pages once per-stock polling accumulates ~700 requests at ~5 QPS
# (≈2.5 min into a full-market sync), and the ban holds for the rest of the
# day. Three defenses, all centralized in :func:`_tencent_get`:
#
# 1. Pacing — every Tencent-host request waits ≥0.25s + jitter after the
#    previous one, stretching a full-market sync to ~30 min so the burst
#    pattern that trips the WAF never forms.
# 2. Per-host cooldown — a 501 marks that host untouchable for 5 minutes;
#    calls during the cooldown return ``None`` without touching the network,
#    so the caller immediately falls through to the next source instead of
#    hammering a host that is already annoyed.
# 3. Session rebuild on 501 — fresh connection pool + rotated User-Agent,
#    dropping whatever cookie/connection state the WAF may have fingerprinted.
# ---------------------------------------------------------------------------
from urllib.parse import urlparse  # noqa: E402

_TENCENT_MIN_INTERVAL_SEC = 0.25
_TENCENT_JITTER_SEC = 0.25
_TENCENT_WAF_COOLDOWN_SEC = 300.0
_tencent_last_ts = 0.0
_waf_cooldown_until: dict[str, float] = {}
_tencent_pace_lock = threading.Lock()

# Small UA pool rotated on every session rebuild; all look like ordinary
# browsers (an empty/bot UA is itself a risk signal for these hosts).
_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0 Safari/537.36",
]
_ua_idx = 0


def _reset_http_session() -> None:
    """Rebuild the shared session after a WAF 501 (new pool, rotated UA)."""
    global _http, _ua_idx
    _ua_idx = (_ua_idx + 1) % len(_USER_AGENTS)
    _http = requests.Session()
    _http.trust_env = False
    _http.headers.update({"User-Agent": _USER_AGENTS[_ua_idx]})
    logger.info("rebuilt http session with UA #%d after tencent 501", _ua_idx)


def _tencent_get(url: str, params: dict, timeout: float = 12):
    """Rate-limited GET for any Tencent finance host.

    :return: the ``Response`` on HTTP 2xx, or ``None`` when the host is in
        WAF cooldown, returned 501, or the request failed (logged). ``None``
        is the caller's cue to try its next source — it does NOT mean the
        symbol genuinely has no data.
    """
    global _tencent_last_ts
    host = urlparse(url).netloc
    with _tencent_pace_lock:
        if time.time() < _waf_cooldown_until.get(host, 0.0):
            return None
        now = time.time()
        wait = (
            _TENCENT_MIN_INTERVAL_SEC
            + random.uniform(0, _TENCENT_JITTER_SEC)
            - (now - _tencent_last_ts)
        )
        if wait > 0:
            time.sleep(wait)
        _tencent_last_ts = time.time()
    try:
        r = _http.get(url, params=params, timeout=timeout)
        if r.status_code == 501:
            _waf_cooldown_until[host] = time.time() + _TENCENT_WAF_COOLDOWN_SEC
            _reset_http_session()
            logger.warning(
                "tencent 501 challenge from %s — host cooling down %.0fs",
                host, _TENCENT_WAF_COOLDOWN_SEC,
            )
            return None
        r.raise_for_status()
        return r
    except Exception as e:  # noqa: BLE001 - any failure → None, next source
        logger.warning("tencent request failed (%s): %s", host, e)
        return None

# ---------------------------------------------------------------------------
# Hard wall-clock timeout guard for akshare (which uses ``requests``).
#
# akshare already passes ``timeout=15`` to requests, but a scalar timeout is
# the *idle gap between bytes* — a server that drips one byte every 14s never
# trips it and the call hangs for minutes (observed against eastmoney's
# anti-bot). We wrap each fetch in a wall-clock deadline: a worker thread runs
# the akshare call while the caller waits via ``future.result(timeout=...)``.
# On timeout we abandon the call (the zombie thread is the cost — bounded by
# the pool size) and the callers' ``@retry`` treats it as a retryable failure.
# ---------------------------------------------------------------------------

_FETCH_TIMEOUT_SEC: float = 30.0
# Small shared pool; threads that time out become zombies until their socket
# closes, so keep this low.
_timeout_executor = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="akshare-fetch"
)


def _with_timeout(func, *args, **kwargs):
    """Run ``func(*args, **kwargs)`` under a hard wall-clock deadline.

    Raises :class:`TimeoutError` if the call exceeds ``_FETCH_TIMEOUT_SEC``.
    """
    fut = _timeout_executor.submit(func, *args, **kwargs)
    try:
        return fut.result(timeout=_FETCH_TIMEOUT_SEC)
    except FuturesTimeout:
        logger.warning(
            "fetch %s exceeded %.0fs wall-clock; abandoning",
            getattr(func, "__name__", "call"),
            _FETCH_TIMEOUT_SEC,
        )
        raise TimeoutError(
            f"{getattr(func, '__name__', 'call')} exceeded {_FETCH_TIMEOUT_SEC}s"
        )


# Simple throttle between calls to keep us off eastmoney's naughty list.
_MIN_INTERVAL_SEC: float = 0.5
_last_call_ts: float = 0.0


def _throttle() -> None:
    """Sleep so that successive calls are at least ``_MIN_INTERVAL_SEC`` apart."""
    global _last_call_ts
    now = time.time()
    elapsed = now - _last_call_ts
    if elapsed < _MIN_INTERVAL_SEC:
        time.sleep(_MIN_INTERVAL_SEC - elapsed)
    _last_call_ts = time.time()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def fetch_daily_quotes(
    symbol: str, start_date: str, end_date: str, max_bars: int = 400
) -> list[dict]:
    """Fetch daily OHLCV for one A-share symbol.

    Primary source: the alternate Tencent host ``proxy.finance.qq.com``
    (same qfq series). Promoted 2026-08-30: since 2026-08-26 the classic
    ``web.ifzq.gtimg.cn`` WAF 501-bans sustained per-stock polling (see
    :func:`_tencent_get`), while this host kept serving through the whole
    2026-08-29 catch-up run (45910 rows, 0 failures). Its bars also carry
    ``amount``/``turnover`` (the classic host serves neither).

    Fallback order:
    1. ``proxy.finance.qq.com`` — primary (WAF-tolerant so far, extended
       bars with ``amount``/``turnover``).
    2. ``web.ifzq.gtimg.cn`` — the classic host; 6-field bars, no
       ``amount``/``turnover``. Serves as a second opinion when the primary
       is cooling down (its WAF posture is independent).
    3. akshare (``stock_zh_a_hist`` → eastmoney) — covers the STAR-board
       ``qfq`` codes Tencent sometimes 501s, and fills ``amount``/``turnover``.

    An *empty* result from a source is NOT a failure — it means the symbol
    has no bars in the window, so we return ``[]`` without trying the next.

    :param symbol: 6-digit code, e.g. ``'600519'``.
    :param start_date: ``'YYYYMMDD'`` (inclusive).
    :param end_date: ``'YYYYMMDD'`` (inclusive).
    :param max_bars: Tencent serves "the latest N bars"; this caps N. The
        default 400 covers the daily incremental sync. The server clamps
        very large N to ~640 (verified live 2026-08-19: n=800 → 801 bars,
        n=1300 → 641), so the multi-year history back-fill passes 640 and
        splits the window into chunks by the caller.
    :return: list of dicts with keys ``stock_code, trade_date, open, close,
        high, low, volume, amount, pct_change, turnover``. ``trade_date`` is a
        ``str`` like ``'2026-07-20'`` (parsed downstream). Empty list if the
        upstream frame was empty.
    """
    # Primary: alternate Tencent host — WAF-tolerant, extended bars. See the
    # docstring for why this leads since 2026-08-30.
    rows = _fetch_daily_quotes_tencent(
        symbol, start_date, end_date, url=_TENCENT_KLINE_ALT, extended=True,
        max_bars=max_bars,
    )
    if rows:
        return rows
    # Secondary: the classic ifzq host (independent WAF posture, 6-field
    # bars without amount/turnover).
    rows = _fetch_daily_quotes_tencent(symbol, start_date, end_date, max_bars=max_bars)
    if rows:
        return rows
    # If Tencent returned [] due to an *error* (not a genuine empty window),
    # we can't tell here — but trying eastmoney anyway is cheap and gives the
    # STAR-board qfq codes (which Tencent 501s) a chance. A genuine empty
    # window will just come back empty again, so no harm.
    try:
        _throttle()
        df = _with_timeout(
            ak.stock_zh_a_hist,
            symbol=symbol,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )
        if df is None or df.empty:
            return []
        logger.info(
            "tencent empty for %s, used eastmoney (%d rows)",
            symbol,
            len(df),
        )
        rows = _frame_to_rows(symbol, df)
        # Degraded-frame guard: eastmoney's soft-ban serves well-formed JSON
        # with real OHLC but every derived field blank (成交额=0, 涨跌幅
        # missing — observed 2026-08-17 while IP-blocked). Upserting that
        # would null out pct_change on already-good rows, so treat an
        # all-pct-missing frame as garbage.
        if rows and all(r.get("pct_change") is None for r in rows):
            logger.warning(
                "eastmoney frame for %s has no pct_change on any row — "
                "degraded response, discarding %d rows",
                symbol, len(rows),
            )
            return []
        return rows
    except Exception as e:
        logger.warning(
            "eastmoney fallback failed for %s: %s",
            symbol,
            e,
        )
        return []


def _frame_to_rows(symbol: str, df) -> list[dict]:
    """Convert an akshare daily frame to the canonical row-dict list.

    Shared by the akshare primary path so the fallback does not duplicate the
    column mapping. Expects akshare 1.18.80 column names
    (``日期/开盘/收盘/最高/最低/成交量/成交额/涨跌幅/换手率``).

    Volume unit normalization: akshare (eastmoney) ``成交量`` is in **股**
    (shares), while the Tencent primary source returns **手** (1 手 = 100 股).
    We normalize everything to 手 here so both sources land in the same unit —
    matching A-share quoting convention (行情软件普遍以手展示成交量).
    See :func:`_shares_to_lots` for the divisor.
    """
    out: list[dict] = []
    for _, row in df.iterrows():
        out.append(
            {
                "stock_code": symbol,
                "trade_date": row["日期"],  # str 'YYYY-MM-DD'
                "open": _to_float(row.get("开盘")),
                "close": _to_float(row.get("收盘")),
                "high": _to_float(row.get("最高")),
                "low": _to_float(row.get("最低")),
                "volume": _shares_to_lots(row.get("成交量")),
                "amount": _to_float(row.get("成交额")),
                "pct_change": _to_float(row.get("涨跌幅")),
                "turnover": _to_float(row.get("换手率")),
            }
        )
    return out


def _shares_to_lots(v: Any) -> int | None:
    """Convert an eastmoney ``成交量`` (股 / shares) value to 手 (lots).

    eastmoney reports volume in shares; the Tencent primary source reports it
    in 手 (1 手 = 100 股). Normalizing eastmoney to 手 here keeps the two
    sources consistent in the canonical schema (verified live 2026-08-13:
    601398 amount/volume ≈ close ⇒ eastmoney volume is per-share; Tencent
    realtime quote part[6] is the same day's volume in 手).

    Returns ``None`` when the input is missing. Round-trips through float so a
    decimal string like ``'580823797.0'`` doesn't trip ``int()``.
    """
    shares = _to_float(v)
    if shares is None:
        return None
    return int(shares / 100)


# Tencent front-adjusted daily kline. Both hosts serve the same qfq series;
# all requests go through :func:`_tencent_get` for pacing + 501 cooldown.
# Secondary host since 2026-08-30: its WAF bans sustained per-stock polling
# (see the ``_tencent_get`` block for the 2026-08-26..29 incident), and its
# bars are 6-field (no amount/turnover).
_TENCENT_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
# Primary host since 2026-08-30: carried the whole 2026-08-29 catch-up while
# the classic host was 501-banned, and its bars carry two extra trailing
# fields (turnover, amount) — see ``_fetch_daily_quotes_tencent(extended=True)``.
_TENCENT_KLINE_ALT = (
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
)


def _tencent_prefix(code: str) -> str:
    """Map a 6-digit A-share code to the Tencent exchange prefix.

    ``6/9/5`` → ``sh`` (Shanghai main board / STAR / ETF — we only need the
    equity subset but the prefix rule is uniform), ``8`` → ``bj`` (Beijing),
    everything else → ``sz`` (Shenzhen main board / ChiNet).
    """
    if not code:
        return "sz"
    head = code[0]
    if head in ("6", "9", "5"):
        return "sh"
    if head == "8":
        return "bj"
    return "sz"


def _fetch_daily_quotes_tencent(
    symbol: str,
    start_date: str,
    end_date: str,
    url: str = _TENCENT_KLINE,
    extended: bool = False,
    max_bars: int = 400,
) -> list[dict]:
    """Tencent front-adjusted daily kline for one A-share symbol.

    :param url: which Tencent host to query (primary or the alternate,
        see :data:`_TENCENT_KLINE_ALT`).
    :param extended: parse the alternate host's two extra trailing bar
        fields — ``[7]`` turnover-rate (%) and ``[8]`` turnover-amount in
        万元 (converted here to 元). The primary host's 6-field bars leave
        them ``None``.
    :param max_bars: cap on the requested bar count (see
        :func:`fetch_daily_quotes` for the server-side ~640 clamp).

    Tencent serves "the most recent *N* bars" rather than a date range, so we
    estimate *N* from the requested window (calendar-day span + a buffer for
    holidays/weekends) and filter to ``[start_date, end_date]`` on the client
    side.

    Returns rows with the same schema as :func:`fetch_daily_quotes`, except
    ``amount`` and ``turnover`` are ``None`` unless ``extended``.

    On any error returns ``[]`` — the caller will record the code as failed so
    the 23:00 retry can pick it up.
    """
    from datetime import datetime

    # Estimate how many bars cover the window. Trading days ≈ 5/7 of calendar
    # days; add a 50-bar buffer so a window starting mid-week still fits, and
    # cap so a wildly large window doesn't ask Tencent for thousands of bars.
    try:
        start_dt = datetime.strptime(start_date, "%Y%m%d").date()
        end_dt = datetime.strptime(end_date, "%Y%m%d").date()
    except ValueError:
        return []
    span = max((end_dt - start_dt).days, 7)
    n = min(int(span * 7 / 5) + 50, max_bars)

    sym = f"{_tencent_prefix(symbol)}{symbol}"
    # Pass the window dates explicitly: without them the endpoint serves "the
    # latest N bars", which client-side filtering empties for old windows
    # (verified live 2026-08-19: a 2021-08-19..2022-09-22 chunk returned 0
    # rows without dates, 266 rows with them — and the eastmoney fallback
    # covering those chunks is what trips its anti-bot). The client-side
    # range filter below stays as a belt-and-braces.
    d_start = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
    d_end = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
    try:
        r = _tencent_get(
            url,
            params={"param": f"{sym},day,{d_start},{d_end},{n},qfq"},
            timeout=12,
        )
        if r is None:
            # Cooldown/501/network failure — caller falls through to the
            # next source (this is NOT a genuine empty window).
            return []
        data = (r.json().get("data") or {}).get(sym) or {}
    except Exception as e:  # noqa: BLE001 - any failure → empty, let upstream retry later
        logger.warning(
            "tencent kline failed for %s (%s): %s",
            symbol, urlparse(url).netloc, e,
        )
        return []

    # Tencent field order per bar: [date, open, close, high, low, volume].
    # ``qfqday`` is the front-adjusted series; fall back to plain ``day``.
    raw = data.get("qfqday") or data.get("day") or []
    start_yyyymmdd = start_date
    end_yyyymmdd = end_date
    out: list[dict] = []
    prev_close: float | None = None
    for it in raw:
        if not isinstance(it, list) or len(it) < 6:
            continue
        d_str = it[0]  # 'YYYY-MM-DD'
        d_compact = d_str.replace("-", "")  # 'YYYYMMDD' for range compare
        if d_compact < start_yyyymmdd or d_compact > end_yyyymmdd:
            # Still track prev_close so pct_change is correct once we enter range.
            try:
                prev_close = float(it[2])
            except (TypeError, ValueError):
                prev_close = None
            continue
        close = _to_float(it[2])
        pct = None
        if prev_close and close:
            pct = round((close - prev_close) / prev_close * 100, 4)
        amount = None
        turnover = None
        if extended:
            # Alternate-host bars: [7]=turnover-rate %, [8]=amount in 万元.
            turnover = _to_float(it[7]) if len(it) > 7 and it[7] else None
            raw_amt = _to_float(it[8]) if len(it) > 8 and it[8] else None
            if raw_amt is not None:
                amount = round(raw_amt * 10000, 2)
        out.append(
            {
                "stock_code": symbol,
                "trade_date": d_str,  # 'YYYY-MM-DD', same shape as akshare
                "open": _to_float(it[1]),
                "close": close,
                "high": _to_float(it[3]),
                "low": _to_float(it[4]),
                # Tencent returns volume as a numeric string like
                # '1061011.000'; _to_int would reject the decimal point, so
                # round-trip through float first.
                "volume": int(v) if (v := _to_float(it[5])) is not None else None,
                "amount": amount,  # only the alternate host exposes it
                "pct_change": pct,
                "turnover": turnover,  # only the alternate host exposes it
            }
        )
        prev_close = close
    return out


def fetch_spot_table() -> list[dict]:
    """Whole-market spot snapshot — the full A-share list in ONE logical fetch.

    Primary: akshare ``stock_zh_a_spot_em`` (eastmoney), ~4200 rows in a
    single request. Fallback: Tencent's paginated board-rank API (~4200 rows
    over ~22 requests of 200) — added because eastmoney IP-bans this host
    class for hours after a burst (observed 2026-08-16), and the universe
    refresh must not be single-homed on it.

    Columns returned (canonical keys): ``stock_code, stock_name, close,
    pct_change, turnover, pe, pb, total_mv, circ_mv`` — market values in
    亿元 (the ``stock_pool`` convention). Suspended stocks carry NaN/missing
    cells → ``None``. ``pb`` is ``None`` on the Tencent source (it does not
    expose book value).

    :return: list of dicts, ``[]`` when both sources fail.
    """
    rows = _fetch_spot_table_eastmoney()
    if rows:
        return rows
    logger.warning("spot table: eastmoney empty/failed, trying tencent rank")
    rows = _fetch_spot_table_tencent()
    if rows:
        return rows
    return []


def _mv_yuan_to_yi(v) -> float | None:
    """元 (eastmoney mv unit) → 亿元 (the stock_pool convention)."""
    f = _to_float(v)
    return f / 1e8 if f is not None else None


def _fetch_spot_table_eastmoney() -> list[dict]:
    """Eastmoney whole-market spot via ``ak.stock_zh_a_spot_em``.

    Columns used (akshare 1.18.80): 代码, 名称, 最新价, 涨跌幅, 换手率,
    市盈率-动态, 市净率, 总市值, 流通市值.
    """
    try:
        _throttle()
        df = _with_timeout(ak.stock_zh_a_spot_em)
    except Exception as e:  # noqa: BLE001 - any failure → fall back to tencent
        logger.warning("eastmoney spot table failed: %s", e)
        return []
    if df is None or df.empty:
        return []
    out: list[dict] = []
    for _, row in df.iterrows():
        code = str(row.get("代码", "")).strip()
        if not re.fullmatch(r"\d{6}", code):
            continue
        name = row.get("名称")
        out.append(
            {
                "stock_code": code,
                "stock_name": name if isinstance(name, str) else None,
                "close": _to_float(row.get("最新价")),
                "pct_change": _to_float(row.get("涨跌幅")),
                "turnover": _to_float(row.get("换手率")),
                "pe": _to_float(row.get("市盈率-动态")),
                "pb": _to_float(row.get("市净率")),
                "total_mv": _mv_yuan_to_yi(row.get("总市值")),
                "circ_mv": _mv_yuan_to_yi(row.get("流通市值")),
            }
        )
    return out


# Tencent board-rank endpoint (same proxy.finance.qq.com host as the alt
# daily-K source). Serves the whole A-share market paginated; field names
# verified live 2026-08-17 against known closes.
_TENCENT_RANK = "https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList"


def _fetch_spot_table_tencent(
    board: str = "aStock", page_size: int = 200, max_rows: int = 7000
) -> list[dict]:
    """Tencent whole-market rank as the spot-table fallback.

    Rank fields → canonical keys: ``zxj``→close, ``zdf``→pct_change(%),
    ``hsl``→turnover(%), ``pe_ttm``→pe, ``ltsz``/``zsz``→circ/total market
    value (already 亿元, the stock_pool convention). No ``pb``. Codes come
    prefixed (``sh600519``).
    """
    out: list[dict] = []
    seen: set[str] = set()
    offset = 0
    try:
        while offset < max_rows:
            r = _tencent_get(
                _TENCENT_RANK,
                params={
                    "board_code": board,
                    "sort_type": "price",
                    "direct": "down",
                    "offset": offset,
                    "count": page_size,
                },
                timeout=12,
            )
            if r is None:
                break  # host cooling down / failed mid-pagination
            rank = ((r.json().get("data") or {}).get("rank_list")) or []
            if not rank:
                break
            for it in rank:
                full = str(it.get("code", ""))
                code = full[2:] if len(full) == 8 else ""
                if not re.fullmatch(r"\d{6}", code) or code in seen:
                    continue
                if not str(it.get("stock_type", "")).startswith("GP-A"):
                    continue  # skip funds/bonds/indices riding along
                seen.add(code)
                out.append(
                    {
                        "stock_code": code,
                        "stock_name": it.get("name"),
                        "close": _to_float(it.get("zxj")),
                        "pct_change": _to_float(it.get("zdf")),
                        "turnover": _to_float(it.get("hsl")),
                        "pe": _to_float(it.get("pe_ttm")),
                        "pb": None,  # not served by this endpoint
                        "total_mv": _to_float(it.get("zsz")),
                        "circ_mv": _to_float(it.get("ltsz")),
                    }
                )
            if len(rank) < page_size:
                break
            offset += page_size
    except Exception as e:  # noqa: BLE001 - fallback may fail like the primary
        logger.warning("tencent rank spot table failed (got %d rows): %s", len(out), e)
    return out


def fetch_money_flow(symbol: str) -> list[dict]:
    """Fetch main-force net inflow for one stock.

    TODO(B-later): akshare's ``stock_individual_fund_flow`` returns a frame
    whose schema (and the column that maps to "main net inflow") varies between
    versions. Verify the real columns against a live fetch before implementing,
    so we don't guess the field name. Left as a deliberate stub for now.
    """
    raise NotImplementedError(
        "fetch_money_flow: pending verification of stock_individual_fund_flow schema"
    )


# 只保留最近 N 期报告 —— abstract 接口返回 1990 年代至今约 105 期，研究用不到那么远。
_FIN_PERIODS = 24

# abstract 指标名 → 规范字段（实测 akshare 1.18.80, 600519, 2026-08-24）：
# 「常用指标」区含 基本每股收益 / 净资产收益率(ROE)；「成长能力」区含两个增长率。
_FIN_INDICATOR_MAP = {
    "基本每股收益": "eps",
    "净资产收益率(ROE)": "roe",
    "营业总收入增长率": "revenue_growth",
    "归属母公司净利润增长率": "profit_growth",
}


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def fetch_financial_abstract(symbol: str) -> list[dict]:
    """Fetch per-report financial indicators (roe/eps/growth) for one stock.

    Source: ``ak.stock_financial_abstract(symbol)`` — ONE request per symbol
    (chosen over ``stock_financial_analysis_indicator``, which issues ~29
    paginated requests per symbol — 133k requests for the full market, an
    unacceptable ban risk). Wide pivot: ``选项`` (section) + ``指标`` rows,
    report periods as ``YYYYMMDD`` string columns, values in % for roe and
    the growth rates, 元 for eps.

    :return: list of dicts ``{stock_code, report_date('YYYY-MM-DD'), roe,
        eps, revenue_growth, profit_growth}``, ascending by report_date,
        most recent :data:`_FIN_PERIODS` periods only (the leading — newest —
        period columns). Empty on failure.
    """
    _throttle()
    df = _with_timeout(ak.stock_financial_abstract, symbol=symbol)
    if df is None or df.empty:
        return []

    # indicator name -> {period_compact: value}; first occurrence wins
    # (some names repeat across sections — 常用指标 comes first and is the
    # canonical one).
    series: dict[str, dict[str, float | None]] = {}
    for _, row in df.iterrows():
        name = str(row.get("指标", ""))
        field = _FIN_INDICATOR_MAP.get(name)
        if field is None or field in series:
            continue
        values: dict[str, float | None] = {}
        for col in df.columns[2:]:
            values[str(col)] = _to_float(row.get(col))
        series[field] = values

    # Period columns are ordered NEWEST-first (verified live: 20260630,
    # 20260331, ...); take the leading ones.
    periods = [str(c) for c in df.columns[2:]][:_FIN_PERIODS]
    out: list[dict] = []
    for p in periods:
        report_date = f"{p[:4]}-{p[4:6]}-{p[6:8]}"
        rec = {
            "stock_code": symbol,
            "report_date": report_date,
            "roe": series.get("roe", {}).get(p),
            "eps": series.get("eps", {}).get(p),
            "revenue_growth": series.get("revenue_growth", {}).get(p),
            "profit_growth": series.get("profit_growth", {}).get(p),
        }
        if any(
            rec[k] is not None
            for k in ("roe", "eps", "revenue_growth", "profit_growth")
        ):
            out.append(rec)
    out.sort(key=lambda r: r["report_date"])
    return out


# ============================================================================
# V1.5 data-source fetchers.
#
# Each follows the same shape as ``fetch_daily_quotes``: throttle + retry, then
# return a list of plain dicts keyed by the canonical (english) column names so
# the sync layer never touches Chinese column headers. Column mappings are taken
# from akshare 1.18.80; the eastmoney-backed endpoints occasionally rename
# columns between versions, so callers must tolerate missing keys (``.get``).
# ============================================================================


def fetch_minute_quotes(symbol: str, period: int = 5) -> list[dict]:
    """Fetch intraday minute OHLCV for one A-share symbol.

    :param symbol: 6-digit code, e.g. ``'600519'``.
    :param period: bar size in minutes; accepts 1/5/15/30/60.
    :return: list of dicts with keys ``stock_code, period, trade_date,
        trade_time, open, close, high, low, volume, amount``.
        ``trade_time`` is a ``str`` like ``'2026-07-28 14:30'`` (parsed
        downstream). Empty list if upstream was empty. ``amount`` is ``None``
        when sourced from Tencent (it does not expose turnover-amount).

    Primary source: Tencent minute kline (``ifzq.gtimg.cn/mkline``) — direct
    connection is not anti-bot-blocked, unlike eastmoney. Falls back to
    akshare (``stock_zh_a_hist_min_em`` → eastmoney) only when Tencent returns
    nothing, which also fills the ``amount`` field Tencent omits.
    """
    rows = _fetch_minute_quotes_tencent(symbol, period)
    if rows:
        return rows
    # Fallback: akshare (eastmoney). Fills ``amount`` (Tencent omits it) and
    # covers any period/edge case Tencent's endpoint doesn't serve.
    try:
        _throttle()
        df = _with_timeout(
            ak.stock_zh_a_hist_min_em,
            symbol=symbol,
            period=str(period),
            adjust="qfq",
        )
        if df is None or df.empty:
            return []
        logger.info(
            "tencent minute empty for %s/%dmin, used eastmoney (%d rows)",
            symbol, period, len(df),
        )
        out: list[dict] = []
        for _, row in df.iterrows():
            trade_time = str(row.get("时间", ""))
            out.append(
                {
                    "stock_code": symbol,
                    "period": period,
                    "trade_time": trade_time,
                    "trade_date": _trade_date_from_time(trade_time),
                    "open": _to_float(row.get("开盘")),
                    "close": _to_float(row.get("收盘")),
                    "high": _to_float(row.get("最高")),
                    "low": _to_float(row.get("最低")),
                    # eastmoney 成交量 is in 股; normalize to 手 to match
                    # the Tencent primary path. See _shares_to_lots.
                    "volume": _shares_to_lots(row.get("成交量")),
                    "amount": _to_float(row.get("成交额")),
                }
            )
        return out
    except Exception as e:
        logger.warning("eastmoney minute fallback failed for %s: %s", symbol, e)
        return []


def _fetch_minute_quotes_tencent(
    symbol: str, period: int
) -> list[dict]:
    """Tencent minute kline for one A-share symbol.

    Tencent's mkline endpoint serves "the most recent *N* bars" for a given
    period; it does not take a date range, so we request the latest ~320 bars
    (a couple of trading days) and return all of them. Each bar is
    ``[YYYYMMDDHHMM, open, close, high, low, volume, {}, turnover]``.

    Returns rows with the same schema as :func:`fetch_minute_quotes`, except
    ``amount`` is ``None`` (Tencent does not expose turnover-amount). On any
    error returns ``[]`` so the caller falls back to eastmoney.
    """
    # Tencent period codes mirror the bar size directly: m1/m5/m15/m30/m60.
    _TENCENT_MINUTE_KLINE = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
    sym = f"{_tencent_prefix(symbol)}{symbol}"
    try:
        r = _tencent_get(
            _TENCENT_MINUTE_KLINE,
            params={"param": f"{sym},m{period},,320"},
            timeout=10,
        )
        if r is None:
            return []
        data = (r.json().get("data") or {}).get(sym) or {}
    except Exception as e:  # noqa: BLE001 - any failure → empty, fall back
        logger.warning("tencent minute failed for %s: %s", symbol, e)
        return []

    # Bar key is ``m<period>`` (e.g. ``m5``); ``prec``/``qt`` are metadata.
    raw = data.get(f"m{period}") or []
    out: list[dict] = []
    for it in raw:
        if not isinstance(it, list) or len(it) < 6:
            continue
        # ``202607291405`` → trade_date '2026-07-29', trade_time '2026-07-29 14:05'
        compact = str(it[0])
        if len(compact) < 12:
            continue
        trade_date = f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
        trade_time = f"{trade_date} {compact[8:10]}:{compact[10:12]}"
        out.append(
            {
                "stock_code": symbol,
                "period": period,
                "trade_time": trade_time,
                "trade_date": trade_date,
                "open": _to_float(it[1]),
                "close": _to_float(it[2]),
                "high": _to_float(it[3]),
                "low": _to_float(it[4]),
                # Tencent returns volume as a numeric string; round-trip via
                # float so the decimal point doesn't trip _to_int.
                "volume": int(v) if (v := _to_float(it[5])) is not None else None,
                "amount": None,  # Tencent does not expose turnover-amount
            }
        )
    return out


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def fetch_dragon_tiger(trade_date: str) -> list[dict]:
    """Fetch the dragon-tiger (龙虎榜) stock list for one trade day.

    :param trade_date: ``'YYYYMMDD'``.
    :return: list of dicts with keys ``stock_code, stock_name, trade_date,
        reason, net_buy, buy_amount, sell_amount``. Empty list if none listed.

    Source: ``ak.stock_lhb_detail_em(start_date, end_date)``. Verified columns
    (akshare 1.18.80, live 2026-07-28): ``代码, 名称, 上榜日, 上榜原因,
    龙虎榜净买额, 龙虎榜买入额, 龙虎榜卖出额``.
    """
    _throttle()
    df = _with_timeout(
        ak.stock_lhb_detail_em,
        start_date=trade_date,
        end_date=trade_date,
    )
    if df is None or df.empty:
        return []
    out: list[dict] = []
    for _, row in df.iterrows():
        out.append(
            {
                "stock_code": str(row.get("代码", "")).zfill(6),
                "stock_name": row.get("名称"),
                "trade_date": _to_date_str(row.get("上榜日"), trade_date),
                "reason": row.get("上榜原因"),
                "net_buy": _to_float(row.get("龙虎榜净买额")),
                "buy_amount": _to_float(row.get("龙虎榜买入额")),
                "sell_amount": _to_float(row.get("龙虎榜卖出额")),
            }
        )
    return out


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def fetch_dragon_tiger_seats(stock_code: str, trade_date: str) -> dict:
    """Fetch top-5 buy/sell seats for one dragon-tiger stock on one day.

    :param stock_code: 6-digit code.
    :param trade_date: ``'YYYYMMDD'``.
    :return: ``{"buy": [<seat dict>...], "sell": [<seat dict>...]}`` where each
        seat dict has ``seat_name, buy_amount, sell_amount, net_amount,
        is_institution``. Empty lists if no detail.

    Source: ``ak.stock_lhb_stock_detail_em(symbol, date, flag)`` called twice
    (flag='买入' and flag='卖出'). Columns (akshare 1.18.80): ``营业部名称,
    买入金额, 卖出金额, 净额``. A seat whose name contains ``机构`` is flagged
    ``is_institution=1``.
    """
    _throttle()
    seats: dict[str, list[dict]] = {"buy": [], "sell": []}
    for flag, key in (("买入", "buy"), ("卖出", "sell")):
        df = _with_timeout(
            ak.stock_lhb_stock_detail_em,
            symbol=stock_code,
            date=trade_date,
            flag=flag,
        )
        if df is None or df.empty:
            continue
        for _, row in df.iterrows():
            name = str(row.get("营业部名称", ""))
            seats[key].append(
                {
                    "seat_name": name,
                    "buy_amount": _to_float(row.get("买入金额")),
                    "sell_amount": _to_float(row.get("卖出金额")),
                    "net_amount": _to_float(row.get("净额")),
                    "is_institution": 1 if "机构" in name else 0,
                }
            )
    return seats


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def fetch_north_flow() -> list[dict]:
    """Fetch daily northbound (沪深股通) net inflow history.

    :return: list of dicts with keys ``trade_date, channel, net_buy,
        buy_amount, sell_amount``. ``channel`` is ``sh`` (沪股通) or ``sz``
        (深股通). Most recent day last.

    Source: ``ak.stock_hsgt_hist_em(symbol='沪股通'/'深股通')``. Verified columns
    (akshare 1.18.80, live): ``日期, 当日成交净买额, 买入成交额, 卖出成交额``.
    """
    out: list[dict] = []
    for cn, channel in (("沪股通", "sh"), ("深股通", "sz")):
        _throttle()
        df = _with_timeout(ak.stock_hsgt_hist_em, symbol=cn)
        if df is None or df.empty:
            continue
        for _, row in df.iterrows():
            out.append(
                {
                    "trade_date": _to_date_str(row.get("日期"), None),
                    "channel": channel,
                    "net_buy": _to_float(row.get("当日成交净买额")),
                    "buy_amount": _to_float(row.get("买入成交额")),
                    "sell_amount": _to_float(row.get("卖出成交额")),
                }
            )
    return out


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def fetch_money_flow_detail(symbol: str, market: str) -> list[dict]:
    """Fetch four-tier order net inflow for one stock.

    :param symbol: 6-digit code.
    :param market: ``'sh'`` / ``'sz'``.
    :return: list of dicts with keys ``trade_date, super_net, big_net,
        medium_net, small_net``. Most recent day last.

    Source: ``ak.stock_individual_fund_flow(stock, market)``. Columns
    (akshare 1.18.80): ``日期, 收盘价, 涨跌幅, 主力净流入-净额,
    主力净流入-净占比, 超大单净流入-净额, 大单净流入-净额, 中单净流入-净额,
    小单净流入-净额``.
    """
    _throttle()
    df = _with_timeout(ak.stock_individual_fund_flow, stock=symbol, market=market)
    if df is None or df.empty:
        return []
    out: list[dict] = []
    for _, row in df.iterrows():
        out.append(
            {
                "stock_code": symbol,
                "trade_date": _to_date_str(row.get("日期"), None),
                "super_net": _to_float(row.get("超大单净流入-净额")),
                "big_net": _to_float(row.get("大单净流入-净额")),
                "medium_net": _to_float(row.get("中单净流入-净额")),
                "small_net": _to_float(row.get("小单净流入-净额")),
            }
        )
    return out


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def fetch_sector_list(sector_type: str = "industry") -> list[dict]:
    """Fetch the sector (板块) definition list.

    :param sector_type: ``'industry'`` or ``'concept'``.
    :return: list of dicts with keys ``sector_code, sector_name, sector_type``.

    Source: ``ak.stock_board_industry_name_em()`` / ``stock_board_concept_name_em()``.
    Columns (akshare 1.18.80): ``板块名称, 板块代码``.
    """
    _throttle()
    if sector_type == "concept":
        df = _with_timeout(ak.stock_board_concept_name_em)
    else:
        df = _with_timeout(ak.stock_board_industry_name_em)
    if df is None or df.empty:
        return []
    out: list[dict] = []
    for _, row in df.iterrows():
        out.append(
            {
                "sector_code": str(row.get("板块代码", "")),
                "sector_name": str(row.get("板块名称", "")),
                "sector_type": sector_type,
            }
        )
    return out


def fetch_index_quotes(symbol: str, index_name: str = "") -> list[dict]:
    """Fetch daily index history via the Tencent ``web.ifzq.gtimg.cn`` source.

    :param symbol: exchange-prefixed code, e.g. ``'sh000001'`` (上证指数),
        ``'sz399001'`` (深证成指), ``'sz399006'`` (创业板指).
    :param index_name: display name stored alongside.
    :return: list of dicts (index_code, index_name, trade_date, open, close,
        high, low, amount, pct_change). ``pct_change`` is computed here from
        consecutive closes. Ordered ascending by date. Empty list on error.

    Source: the same ``web.ifzq.gtimg.cn/appstock/app/fqkline/get`` endpoint
    used for individual stocks — it also serves indices and is direct-connect
    stable (unlike ``proxy.finance.qq.com`` that akshare's
    ``stock_zh_index_daily_tx`` uses, which SSL-fails through a proxy and
    pulls the full history year-by-year, slowly and unreliably).

    Each Tencent bar is ``[date, open, close, high, low, volume, {}, amount]``
    (verified live 2026-08-06 for sh000001/sz399001/sz399006).
    """
    _TENCENT_INDEX_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    try:
        r = _tencent_get(
            _TENCENT_INDEX_KLINE,
            params={"param": f"{symbol},day,,,800,qfq"},
            timeout=12,
        )
        if r is None:
            return []
        data = (r.json().get("data") or {}).get(symbol) or {}
    except Exception as e:  # noqa: BLE001 - any failure → empty, logged upstream
        logger.warning("tencent index fetch failed for %s: %s", symbol, e)
        return []

    raw = data.get("qfqday") or data.get("day") or []
    out: list[dict] = []
    prev_close: float | None = None
    for it in raw:
        if not isinstance(it, list) or len(it) < 6:
            continue
        d_str = it[0]  # 'YYYY-MM-DD'
        close = _to_float(it[2])
        pct = None
        if prev_close and close:
            pct = round((close - prev_close) / prev_close * 100, 4)
        # Tencent index bar: [date, open, close, high, low, volume, {}, amount]
        amount = _to_float(it[7]) if len(it) > 7 else None
        out.append(
            {
                "index_code": symbol,
                "index_name": index_name,
                "trade_date": d_str,
                "open": _to_float(it[1]),
                "close": close,
                "high": _to_float(it[3]),
                "low": _to_float(it[4]),
                "amount": amount,
                "pct_change": pct,
            }
        )
        prev_close = close
    return out


def fetch_trade_calendar() -> list:
    """A-share trading dates (past and future) via akshare/sina.

    Used by the scheduler to skip weekend/holiday syncs instead of firing a
    full-market pull that can only write nothing (and only burns WAF
    goodwill). Values are ``datetime.date``. Raises on failure — callers
    fall back to the weekday-only judgment.
    """
    _throttle()
    df = _with_timeout(ak.tool_trade_date_hist_sina)
    out = []
    for v in df["trade_date"]:
        if isinstance(v, datetime):
            out.append(v.date())
        elif isinstance(v, date):
            out.append(v)
        else:
            out.append(date.fromisoformat(str(v)))
    return out


def _to_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        # akshare returns pandas NaN for missing cells; float('nan') parses
        # fine but is meaningless downstream, so normalise to None.
        import math

        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


# ============================================================================
# V2 阶段 N1：新闻资讯采集（BP-V2-008）。
#
# 复用 V1.5 的 ``_with_timeout`` 30s 墙钟超时保护 + ``_throttle`` + ``@retry``
# —— eastmoney/财联社的反爬同样会让 akshare 的 ``requests`` 调用挂起，必须用
# 硬墙钟兜底。返回的 dict 字段名统一为英文，sync 层不碰中文表头。
#
# 三个来源（akshare 1.18.80 实测列名）：
# * ``stock_info_global_cls``  财联社 24h 电报  — 标题/内容/发布日期/发布时间
# * ``stock_info_global_em``   东财全球财经快讯   — 标题/摘要/发布时间/链接
# * ``stock_news_em(symbol)``  东财个股资讯      — 关键词/新闻标题/新闻内容/
#                                                 发布时间/文章来源
# ============================================================================


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def fetch_news_cls() -> list[dict]:
    """抓取财联社（cls）24h 实时电报。

    :return: list of dicts，键为 ``pub_time, title, content, source``，其中
        ``source='cls'``。``pub_time`` 为 :class:`datetime.datetime`（由
        发布日期 + 发布时间 拼装）。空列表表示上游无数据。

    Source: ``ak.stock_info_global_em`` 不带参数；实测列 ``标题/内容/发布日期/
    发布时间``。
    """
    _throttle()
    df = _with_timeout(ak.stock_info_global_cls)
    if df is None or df.empty:
        return []
    out: list[dict] = []
    for _, row in df.iterrows():
        pub_time = _combine_date_time(row.get("发布日期"), row.get("发布时间"))
        title = row.get("标题") or ""
        content = row.get("内容")
        # 财联社标题有时为空（电报条目），用正文首句兜底，便于后续展示。
        if not title and content:
            title = str(content).split("】", 1)[-1].split("\n", 1)[0][:300]
        out.append(
            {
                "pub_time": pub_time,
                "title": str(title)[:300] if title else None,
                "content": str(content) if content is not None else None,
                "source": "cls",
            }
        )
    return out


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def fetch_news_em() -> list[dict]:
    """抓取东方财富（em）全球财经实时快讯。

    :return: list of dicts，键为 ``pub_time, title, content, source``，其中
        ``source='em'``。``content`` 取自 ``摘要`` 列。

    Source: ``ak.stock_info_global_em``；实测列 ``标题/摘要/发布时间/链接``，
    ``发布时间`` 形如 ``'2026-08-04 08:50:03'``。
    """
    _throttle()
    df = _with_timeout(ak.stock_info_global_em)
    if df is None or df.empty:
        return []
    out: list[dict] = []
    for _, row in df.iterrows():
        title = row.get("标题")
        out.append(
            {
                "pub_time": _parse_datetime(row.get("发布时间")),
                "title": str(title)[:300] if title else None,
                "content": str(row.get("摘要")) if row.get("摘要") is not None else None,
                "source": "em",
            }
        )
    return out


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def fetch_news_em_stock(symbol: str) -> list[dict]:
    """抓取东方财富个股资讯（带关联个股代码）。

    :param symbol: 6 位个股代码，例如 ``'600519'``。
    :return: list of dicts，键为 ``pub_time, title, content, source,
        stock_codes``，其中 ``source='em-stock'``，``stock_codes=[symbol]``。

    Source: ``ak.stock_news_em(symbol)``；实测列 ``关键词/新闻标题/新闻内容/
    发布时间/文章来源``，``发布时间`` 形如 ``'2026-08-03 18:43:00'``。
    """
    _throttle()
    df = _with_timeout(ak.stock_news_em, symbol=symbol)
    if df is None or df.empty:
        return []
    out: list[dict] = []
    for _, row in df.iterrows():
        title = row.get("新闻标题")
        content = row.get("新闻内容")
        out.append(
            {
                "pub_time": _parse_datetime(row.get("发布时间")),
                "title": str(title)[:300] if title else None,
                "content": str(content) if content is not None else None,
                "source": "em-stock",
                "stock_codes": [symbol],
            }
        )
    return out


def fetch_news(source: str = "cls") -> list[dict]:
    """新闻资讯统一入口：按 ``source`` 分派到具体抓取函数。

    :param source: ``'cls'``（财联社，默认）/ ``'em'``（东财全球）/ ``'em-stock'``
        （东财个股，需要先指定 ``symbol``，见 :func:`fetch_news_em_stock`）。
    :return: 合并后的资讯 dict 列表（字段同各分派函数）。

    之所以再封一层：sync 层只关心“拉一批新闻”，不关心具体接口；这里集中做
    source 路由，便于以后扩 ``news_cctv`` 等新源而无需改 sync。
    """
    if source == "em":
        return fetch_news_em()
    if source == "em-stock":
        # em-stock 需要个股代码，这里返回空（调用方应直接用
        # fetch_news_em_stock）；保留分支只为路由完整性。
        return []
    # 默认 cls
    return fetch_news_cls()


def _to_int(v: Any) -> int | None:
    try:
        if v is None:
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _to_date_str(v: Any, fallback: str | None) -> str | None:
    """Coerce a date-like value to a ``'YYYY-MM-DD'`` string.

    akshare returns ``日期``/``上榜日`` as a ``datetime.date`` (or sometimes a
    pandas ``Timestamp``); normalise to ISO string. Returns ``fallback`` (the
    caller's request date as ``YYYYMMDD``) when parsing fails — matching the
    request date is usually correct for same-day fetches.
    """
    if v is None:
        return fallback[:4] + "-" + fallback[4:6] + "-" + fallback[6:8] if fallback else None
    try:
        s = str(v)
        # already ISO?
        if len(s) >= 10 and s[4] == "-":
            return s[:10]
    except Exception:
        pass
    try:
        return getattr(v, "strftime", lambda f: None)("%Y-%m-%d")
    except Exception:
        return None


def _trade_date_from_time(trade_time: str) -> str | None:
    """Extract the ``'YYYY-MM-DD'`` trade date from a minute-bar timestamp.

    ``trade_time`` from akshare looks like ``'2026-07-28 14:30'``. Returns the
    leading date portion, or None if unparseable.
    """
    if not trade_time or len(trade_time) < 10:
        return None
    return trade_time[:10]


def _parse_datetime(v: Any) -> datetime | None:
    """把 akshare 返回的时间值统一解析为 :class:`datetime.datetime`。

    覆盖三种输入：
    * ``'YYYY-MM-DD HH:MM:SS'`` / ``'YYYY-MM-DD HH:MM'`` 字符串
      （东财 stock_info_global_em / stock_news_em 实际返回）
    * 已是 :class:`datetime.datetime` / :class:`datetime.date`
    * pandas ``Timestamp``（带 ``to_pydatetime``）
    解析失败返回 None（不抛异常 —— sync 层会把 None 写入 NULL）。
    """
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    # pandas Timestamp 等
    to_py = getattr(v, "to_pydatetime", None)
    if callable(to_py):
        try:
            return to_py()
        except Exception:
            pass
    try:
        s = str(v).strip()
    except Exception:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            # 截到该格式所需长度，容忍尾部多余字符（如时区）。
            return datetime.strptime(s[:_FMT_LEN[fmt]], fmt)
        except ValueError:
            continue
    return None


# 各 format 对应需要截取的字符串长度。
_FMT_LEN = {
    "%Y-%m-%d %H:%M:%S": 19,
    "%Y-%m-%d %H:%M": 16,
    "%Y-%m-%d": 10,
}


def _combine_date_time(d: Any, t: Any) -> datetime | None:
    """把财联社的 ``发布日期`` + ``发布时间`` 两列拼成一个 :class:`datetime`。

    财联社接口分开返回 ``datetime.date`` 和 ``datetime.time``；任意一项缺失
    则返回 None。
    """
    if d is None or t is None:
        return None
    try:
        d_part = d if isinstance(d, date) else _parse_datetime(d)
        t_part = t if isinstance(t, dtime) else None
        if d_part is None or t_part is None:
            return None
        return datetime.combine(d_part, t_part)
    except Exception:
        return None
