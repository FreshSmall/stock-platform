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

    :param symbol: 6-digit code, e.g. ``'600519'``.
    :param start_date: ``'YYYYMMDD'`` (inclusive).
    :param end_date: ``'YYYYMMDD'`` (inclusive).
    :return: list of dicts with keys ``stock_code, trade_date, open, close,
        high, low, volume, amount, pct_change, turnover``. ``trade_date`` is a
        ``str`` like ``'2026-07-20'`` (parsed downstream). Empty list if the
        upstream frame was empty.
    """
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
