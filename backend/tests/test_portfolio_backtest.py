"""Tests for the multi-factor portfolio backtest (V2.2 BP-V2.2-005).

DB-backed end-to-end on real data with a small universe; rows created by the
run are cleaned up afterwards.
"""

from datetime import date

from sqlalchemy import delete, select

from app.models.backtest import SaBacktestResult, SaBacktestRun
from app.services import portfolio_backtest_service as pbs


def test_run_mf_backtest_end_to_end(db_session):
    res = pbs.run_mf_backtest(
        db_session,
        preset="v2_reversal",
        start=date(2026, 5, 1),
        end=date(2026, 8, 31),
        freq="W",
        top_n=5,
        initial_cash=100_000.0,
        pool="pit",
        only_tradable=True,
        liquidity_top_k=150,
        user_id=None,
    )
    try:
        navs = [p["value"] for p in res["nav"]]
        assert navs[0] == 100_000.0  # starts at initial cash, flat before exec
        assert all(v > 0 for v in navs)
        m = res["metrics"]
        assert m["n_rebalances"] >= 4
        assert m["total_cost"] > 0
        assert -1.0 <= m["max_drawdown"] <= 0
        for t in res["turnover_series"]:
            assert 0.0 <= t["turnover"] <= 2.0
        # every rebalance buys in board lots and pays fees
        for reb in res["rebalances"]:
            for b in reb["buys"]:
                assert b["shares"] % 100 == 0
                assert b["fee"] > 0
        # persisted and queryable
        assert res["run_id"].startswith("bt-")
        run = db_session.execute(
            select(SaBacktestRun).where(SaBacktestRun.run_id == res["run_id"])
        ).scalar_one_or_none()
        assert run is not None and run.status == "done"
        assert run.strategy == "mf_portfolio"
        result = db_session.execute(
            select(SaBacktestResult).where(SaBacktestResult.run_id == res["run_id"])
        ).scalar_one()
        assert result.equity_curve
    finally:
        db_session.execute(
            delete(SaBacktestResult).where(SaBacktestResult.run_id == res["run_id"])
        )
        db_session.execute(
            delete(SaBacktestRun).where(SaBacktestRun.run_id == res["run_id"])
        )
        db_session.commit()


def test_rejects_non_panel_factors(db_session):
    import pytest

    with pytest.raises(ValueError, match="面板化"):
        pbs.run_mf_backtest(
            db_session,
            factors=[{"code": "pe", "weight": 1.0}],
            start=date(2026, 5, 1),
            end=date(2026, 8, 31),
        )


def test_unknown_preset_raises(db_session):
    import pytest

    with pytest.raises(ValueError, match="unknown preset"):
        pbs.run_mf_backtest(
            db_session,
            preset="nope",
            start=date(2026, 5, 1),
            end=date(2026, 8, 31),
        )
