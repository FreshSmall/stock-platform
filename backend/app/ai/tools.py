"""Function Calling tools for the AI assistant.

Each tool wraps an existing service and returns JSON-serializable data. Tools
open their own DB session (the LLM tool-call boundary doesn't carry a Session).

These are the functions the LLM (Task E2) binds via ``model.bind_tools``; they
do NOT call the LLM themselves. Each takes primitive args so the LLM's tool-call
arguments map cleanly, and returns a plain dict so the result can be fed back
into the conversation as JSON content.
"""

from datetime import date, timedelta

from langchain_core.tools import tool

from app.core.database import SessionLocal
from app.services import indicator_service, market_service


@tool
def query_kline(code: str, days: int = 60) -> dict:
    """查询某只股票近 N 天的日K行情数据（收盘价、涨跌幅、成交量）。

    Args:
        code: 6位股票代码，如 "600519"
        days: 查询近多少个交易日，默认60

    Returns:
        dict: {code, bars: [{date, close, pct_change, volume}, ...]}
    """
    end = date.today()
    start = end - timedelta(days=int(days) * 2)  # overscan for non-trading days
    db = SessionLocal()
    try:
        rows = market_service.get_kline(db, code, start=start, end=end)
    finally:
        db.close()
    bars = [
        {
            "date": r.trade_date.isoformat(),
            "close": float(r.close) if r.close is not None else None,
            "pct_change": float(r.pct_change) if r.pct_change is not None else None,
            "volume": r.volume,
        }
        for r in rows[-int(days) :]
    ]
    return {"code": code, "bars": bars}


@tool
def query_stock_info(code: str) -> dict:
    """查询某只股票的基础信息（名称、行业、市值、PE、PB）。

    Args:
        code: 6位股票代码

    Returns:
        dict: 股票基础信息，找不到时返回 {"code": code, "found": false}
    """
    db = SessionLocal()
    try:
        info = market_service.get_stock_info(db, code)
    finally:
        db.close()
    if info is None:
        return {"code": code, "found": False}
    return {
        "code": code,
        "found": True,
        "name": info.stock_name,
        "exchange": info.exchange,
        "industry": info.industry,
        "total_mv": float(info.total_mv) if info.total_mv is not None else None,
        "pe": float(info.pe) if info.pe is not None else None,
        "pb": float(info.pb) if info.pb is not None else None,
    }


@tool
def query_macd(code: str, days: int = 60) -> dict:
    """查询某只股票近 N 天的 MACD 指标（dif、dea、macd柱）。

    Args:
        code: 6位股票代码
        days: 查询近多少个交易日

    Returns:
        dict: {code, macd: [{date, dif, dea, macd}, ...]}
    """
    import pandas as pd

    end = date.today()
    start = end - timedelta(days=int(days) * 2)
    db = SessionLocal()
    try:
        rows = market_service.get_kline(db, code, start=start, end=end)
    finally:
        db.close()
    if not rows:
        return {"code": code, "macd": []}
    closes = pd.Series([float(r.close) for r in rows if r.close is not None])
    dates = [r.trade_date.isoformat() for r in rows]
    df = indicator_service.calc_macd(closes)
    out = []
    for d, row in zip(dates, df.to_dict(orient="records")):
        out.append(
            {
                "date": d,
                "dif": None if pd.isna(row["dif"]) else float(row["dif"]),
                "dea": None if pd.isna(row["dea"]) else float(row["dea"]),
                "macd": None if pd.isna(row["macd"]) else float(row["macd"]),
            }
        )
    return {"code": code, "macd": out[-int(days) :]}


@tool
def search_stocks_by_keyword(keyword: str, limit: int = 10) -> dict:
    """按关键词（代码/名称）搜索股票。

    Args:
        keyword: 搜索关键词
        limit: 最多返回条数

    Returns:
        dict: {items: [{stock_code, stock_name, exchange, industry}, ...]}
    """
    db = SessionLocal()
    try:
        rows = market_service.search_stocks(db, keyword, limit=int(limit))
    finally:
        db.close()
    items = [
        {
            "stock_code": r.stock_code,
            "stock_name": r.stock_name,
            "exchange": r.exchange,
            "industry": r.industry,
        }
        for r in rows
    ]
    return {"items": items}


@tool
def run_backtest_light(strategy: str, code: str, days: int = 365) -> dict:
    """对单只股票快速回测某策略（默认近一年），返回核心指标。

    Args:
        strategy: 策略名，目前支持 "ma" 或 "macd"
        code: 6位股票代码
        days: 回测天数（默认365）

    Returns:
        dict: {strategy, code, return_rate, max_drawdown, sharpe, win_rate, trade_count}
    """
    from decimal import Decimal

    from app.services import backtest_service

    end = date.today()
    start = end - timedelta(days=int(days))
    db = SessionLocal()
    try:
        params = {"fast": 5, "slow": 20} if strategy == "ma" else {}
        result = backtest_service.run_backtest(
            db,
            strategy=strategy,
            params=params,
            stock_pool=[code],
            start_date=start,
            end_date=end,
            initial_cash=Decimal("100000"),
        )
    finally:
        db.close()
    return {
        "strategy": strategy,
        "code": code,
        "return_rate": result["return_rate"],
        "max_drawdown": result["max_drawdown"],
        "sharpe": result["sharpe"],
        "win_rate": result["win_rate"],
        "trade_count": result["trade_count"],
    }


ALL_TOOLS = [
    query_kline,
    query_stock_info,
    query_macd,
    search_stocks_by_keyword,
    run_backtest_light,
]
