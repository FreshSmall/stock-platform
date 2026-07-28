"""Integration tests for the Function Calling tools.

These hit the REAL ``stock_analysis`` DB (no mocks) — they verify the data
layer plumbing that the LLM tool calls will rely on, not the LLM itself. The
known-good stock 600519 (贵州茅台) is present in both ``daily_prices`` and
``stock_pool`` (see test_db.py / models), so it is the canonical fixture.
"""

from app.ai import tools


def test_query_kline_returns_bars():
    result = tools.query_kline.invoke({"code": "600519", "days": 30})
    assert result["code"] == "600519"
    assert isinstance(result["bars"], list)
    assert len(result["bars"]) > 0
    assert "close" in result["bars"][0]


def test_query_kline_unknown_code_empty():
    result = tools.query_kline.invoke({"code": "ZZNODATA", "days": 30})
    assert result["bars"] == []


def test_query_stock_info_known():
    result = tools.query_stock_info.invoke({"code": "600519"})
    assert result["found"] is True
    assert "茅台" in (result.get("name") or "")


def test_query_stock_info_unknown():
    result = tools.query_stock_info.invoke({"code": "ZZNODATA"})
    assert result["found"] is False


def test_query_macd_returns_data():
    result = tools.query_macd.invoke({"code": "600519", "days": 30})
    assert "macd" in result
    assert len(result["macd"]) > 0
    assert "dif" in result["macd"][-1]


def test_search_stocks_by_keyword():
    result = tools.search_stocks_by_keyword.invoke({"keyword": "茅台", "limit": 5})
    codes = [it["stock_code"] for it in result["items"]]
    assert "600519" in codes


def test_run_backtest_light_ma():
    result = tools.run_backtest_light.invoke(
        {"strategy": "ma", "code": "600519", "days": 180}
    )
    assert result["strategy"] == "ma"
    assert "return_rate" in result
    assert "max_drawdown" in result
    assert "sharpe" in result


def test_all_tools_registered():
    names = {t.name for t in tools.ALL_TOOLS}
    assert {
        "query_kline",
        "query_stock_info",
        "query_macd",
        "search_stocks_by_keyword",
        "run_backtest_light",
    } <= names
