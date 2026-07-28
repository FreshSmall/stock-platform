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
from typing import Any

import akshare as ak
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

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
    df = ak.stock_zh_a_hist(
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


def _to_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> int | None:
    try:
        if v is None:
            return None
        return int(v)
    except (TypeError, ValueError):
        return None
