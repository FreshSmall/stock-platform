"""Integration tests for the backtest engine (Task C3).

These run against the REAL ``stock_analysis`` DB (see ``conftest.py``) using
real 600519 (贵州茅台) K-line data and the registered ``ma`` strategy. No
mocks: the goal is to confirm the full DB -> DataFrame -> Cerebro -> metrics
pipeline produces sane numbers.

The persistence test (``test_create_and_execute_persists``) writes to the
prod ``sa_backtest_run`` / ``sa_backtest_result`` tables — it cleans up its own
rows at the end and asserts the cleanup succeeded so no test data leaks.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.backtest import SaBacktestResult, SaBacktestRun
from app.services import backtest_service


def test_run_backtest_ma_600519(db_session):
    """End-to-end MA backtest on real 600519 data returns sane headline metrics."""
    result = backtest_service.run_backtest(
        db_session,
        strategy="ma",
        params={"fast": 5, "slow": 20},
        stock_pool=["600519"],
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        initial_cash=Decimal("100000"),
    )
    # headline metrics present
    assert "return_rate" in result
    assert "max_drawdown" in result
    assert "sharpe" in result
    assert "win_rate" in result
    assert "equity_curve" in result
    assert isinstance(result["equity_curve"], list)
    assert result["final_value"] > 0
    # max drawdown is reported as a non-negative percentage (e.g. 12.5 = 12.5%)
    assert result["max_drawdown"] >= 0
    # trade_count matches the per-trade list length
    assert result["trade_count"] == len(result["trades"])
    # win_rate is a percentage in [0, 100]
    assert 0.0 <= result["win_rate"] <= 100.0


def test_run_backtest_unknown_strategy_raises(db_session):
    """An unregistered/unavailable strategy name raises ValueError."""
    with pytest.raises(ValueError, match="unknown"):
        backtest_service.run_backtest(
            db_session,
            strategy="bogus",
            params={},
            stock_pool=["600519"],
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
        )


def test_run_backtest_no_data_raises(db_session):
    """A code with no rows in the window raises ValueError."""
    with pytest.raises(ValueError, match="no kline"):
        backtest_service.run_backtest(
            db_session,
            strategy="ma",
            params={"fast": 5, "slow": 20},
            stock_pool=["ZZNODATA"],
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
        )


def test_create_and_execute_persists(db_session):
    """create_backtest_run + execute_and_store writes run+result rows.

    Writes to prod — cleans up its own rows afterwards and asserts the
    cleanup so no test data leaks into the tables.
    """
    # NOTE: 600519 K-line only exists from 2025-06-16 in this DB, so the
    # window must extend past that date to clear the 30-bar minimum. The spec
    # wrote 2025-06-30 as end_date but that captures only 11 bars; using the
    # full year keeps the test faithful (real MA backtest) while passing.
    run = backtest_service.create_backtest_run(
        db_session,
        user_id=None,
        req={
            "strategy": "ma",
            "params": {"fast": 5, "slow": 20},
            "stock_pool": ["600519"],
            "start_date": date(2025, 1, 1),
            "end_date": date(2025, 12, 31),
            "initial_cash": Decimal("100000"),
        },
    )
    assert run.status == "pending"

    result = backtest_service.execute_and_store(db_session, run)
    assert run.status == "done"
    assert result.return_rate is not None

    try:
        # the rows exist
        assert backtest_service.get_run(db_session, run.run_id) is not None
        assert backtest_service.get_result(db_session, run.run_id) is not None
    finally:
        # CLEANUP: delete in dependency order (result first, then run).
        db_session.query(SaBacktestResult).filter_by(run_id=run.run_id).delete()
        db_session.query(SaBacktestRun).filter_by(run_id=run.run_id).delete()
        db_session.commit()

    # verify cleanup
    assert backtest_service.get_run(db_session, run.run_id) is None
    assert backtest_service.get_result(db_session, run.run_id) is None
