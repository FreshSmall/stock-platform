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
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import date, datetime, time as dtime
from typing import Any

import akshare as ak
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

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
    symbol: str, start_date: str, end_date: str
) -> list[dict]:
    """Fetch daily OHLCV for one A-share symbol.

    Primary source: akshare (``stock_zh_a_hist`` → eastmoney). On repeated
    failure (eastmoney anti-bot / network), falls back to the Tencent
    front-adjusted kline source, which is empirically stable where eastmoney
    is not. The fallback rows carry ``amount=None`` and ``turnover=None``
    (Tencent does not expose them) and a self-computed ``pct_change``.

    An *empty* akshare frame is NOT a failure — it means the symbol genuinely
    has no bars in the window, so we return ``[]`` without invoking Tencent.

    :param symbol: 6-digit code, e.g. ``'600519'``.
    :param start_date: ``'YYYYMMDD'`` (inclusive).
    :param end_date: ``'YYYYMMDD'`` (inclusive).
    :return: list of dicts with keys ``stock_code, trade_date, open, close,
        high, low, volume, amount, pct_change, turnover``. ``trade_date`` is a
        ``str`` like ``'2026-07-20'`` (parsed downstream). Empty list if the
        upstream frame was empty.
    """
    # Primary: akshare (eastmoney). @retry above retries transient blips; if
    # all 3 attempts fail the exception propagates here and we fall back.
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
        return _frame_to_rows(symbol, df)
    except Exception as e:
        logger.warning(
            "akshare failed for %s, falling back to Tencent: %s",
            symbol,
            e,
        )
    # Fallback: Tencent (stable, no IP ban).
    return _fetch_daily_quotes_tencent(symbol, start_date, end_date)


def _frame_to_rows(symbol: str, df) -> list[dict]:
    """Convert an akshare daily frame to the canonical row-dict list.

    Shared by the akshare primary path so the fallback does not duplicate the
    column mapping. Expects akshare 1.18.80 column names
    (``日期/开盘/收盘/最高/最低/成交量/成交额/涨跌幅/换手率``).
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
                "volume": _to_int(row.get("成交量")),
                "amount": _to_float(row.get("成交额")),
                "pct_change": _to_float(row.get("涨跌幅")),
                "turnover": _to_float(row.get("换手率")),
            }
        )
    return out


# Tencent front-adjusted daily kline — the eastmoney anti-bot fallback.
# Ported from Vibe-Research's ``_kline_tencent``; Tencent does not rate-limit
# by IP and returns in well under a second, making it a reliable secondary.
_TENCENT_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


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
    symbol: str, start_date: str, end_date: str
) -> list[dict]:
    """Tencent front-adjusted daily kline fallback for one A-share symbol.

    Tencent serves "the most recent *N* bars" rather than a date range, so we
    estimate *N* from the requested window (calendar-day span + a buffer for
    holidays/weekends, capped at 400) and filter to ``[start_date, end_date]``
    on the client side.

    Returns rows with the same schema as :func:`fetch_daily_quotes`, except:
    * ``amount`` and ``turnover`` are ``None`` (Tencent does not expose them).
    * ``pct_change`` is computed from consecutive closes (Tencent omits it).

    On any error returns ``[]`` — the caller will record the code as failed so
    the 23:00 retry can pick it up.
    """
    import requests
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
    n = min(int(span * 7 / 5) + 50, 400)

    sym = f"{_tencent_prefix(symbol)}{symbol}"
    try:
        r = requests.get(
            _TENCENT_KLINE,
            params={"param": f"{sym},day,,,{n},qfq"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=12,
        )
        r.raise_for_status()
        data = (r.json().get("data") or {}).get(sym) or {}
    except Exception as e:  # noqa: BLE001 - any failure → empty, let upstream retry later
        logger.warning("tencent fallback failed for %s: %s", symbol, e)
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
                "amount": None,  # Tencent does not expose turnover-amount
                "pct_change": pct,
                "turnover": None,  # Tencent does not expose turnover-rate
            }
        )
        prev_close = close
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


def fetch_financial_abstract(symbol: str) -> list[dict]:
    """Fetch financial abstract (ROE/EPS/growth) for one stock.

    TODO(B-later): ``stock_financial_abstract`` returns a wide frame with
    report-period columns that need pivoting. Implement once the target fields
    (roe, eps, revenue_growth, profit_growth) are mapped. Left as a stub.
    """
    raise NotImplementedError(
        "fetch_financial_abstract: pending schema mapping of stock_financial_abstract"
    )


# ============================================================================
# V1.5 data-source fetchers.
#
# Each follows the same shape as ``fetch_daily_quotes``: throttle + retry, then
# return a list of plain dicts keyed by the canonical (english) column names so
# the sync layer never touches Chinese column headers. Column mappings are taken
# from akshare 1.18.80; the eastmoney-backed endpoints occasionally rename
# columns between versions, so callers must tolerate missing keys (``.get``).
# ============================================================================


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def fetch_minute_quotes(symbol: str, period: int = 5) -> list[dict]:
    """Fetch intraday minute OHLCV for one A-share symbol.

    :param symbol: 6-digit code, e.g. ``'600519'``.
    :param period: bar size in minutes; akshare accepts 1/5/15/30/60.
    :return: list of dicts with keys ``stock_code, period, trade_date,
        trade_time, open, close, high, low, volume, amount``.
        ``trade_time`` is a ``str`` like ``'2026-07-28 14:30'`` (parsed
        downstream). Empty list if upstream was empty.

    Source: ``ak.stock_zh_a_hist_min_em(symbol, period=str(period), adjust='qfq')``.
    Columns (akshare 1.18.80): ``时间, 开盘, 收盘, 最高, 最低, 成交量, 成交额,
    最新价``. ``时间`` carries both the date and the minute.
    """
    _throttle()
    df = _with_timeout(
        ak.stock_zh_a_hist_min_em,
        symbol=symbol,
        period=str(period),
        adjust="qfq",
    )
    if df is None or df.empty:
        return []
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
                "volume": _to_int(row.get("成交量")),
                "amount": _to_float(row.get("成交额")),
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


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def fetch_index_quotes(symbol: str, index_name: str = "") -> list[dict]:
    """Fetch daily index history via the Tencent source (避开 push2 反爬).

    :param symbol: exchange-prefixed code, e.g. ``'sh000001'`` (上证指数),
        ``'sz399001'`` (深证成指), ``'sz399006'`` (创业板指).
    :param index_name: display name stored alongside.
    :return: list of dicts (index_code, index_name, trade_date, open, close,
        high, low, amount, pct_change). ``pct_change`` is computed here from
        consecutive closes. Ordered ascending by date.

    Source: ``ak.stock_zh_index_daily_tx(symbol)`` — the Tencent endpoint,
    which is stable where the eastmoney ``push2*`` endpoints are not. Verified
    columns (akshare 1.18.80, live): ``date, open, close, high, low, amount``.
    """
    _throttle()
    df = _with_timeout(ak.stock_zh_index_daily_tx, symbol=symbol)
    if df is None or df.empty:
        return []
    df = df.sort_values("date").reset_index(drop=True)
    out: list[dict] = []
    prev_close: float | None = None
    for _, row in df.iterrows():
        close = _to_float(row.get("close"))
        pct = None
        if prev_close and close:
            pct = round((close - prev_close) / prev_close * 100, 4)
        out.append(
            {
                "index_code": symbol,
                "index_name": index_name,
                "trade_date": _to_date_str(row.get("date"), None),
                "open": _to_float(row.get("open")),
                "close": close,
                "high": _to_float(row.get("high")),
                "low": _to_float(row.get("low")),
                "amount": _to_float(row.get("amount")),
                "pct_change": pct,
            }
        )
        prev_close = close
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
