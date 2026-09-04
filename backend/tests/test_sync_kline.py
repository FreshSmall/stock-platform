"""Tests for app.data.sync_kline (V2.1 raw store + adjust factors).

SAFETY (same policy as test_sync_daily): live DB, sentinel stock code
``ZZT21`` (not a valid A-share code), akshare always mocked, sentinel rows
scrubbed in teardown. Factor-chain math is tested as pure functions first.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select

from app.data import sync_kline
from app.models.kline import SaAdjustFactor, SaKlineDaily

SENTINEL = "ZZT21"


@pytest.fixture
def cleanup_sentinel(db_session):
    for model in (SaKlineDaily, SaAdjustFactor):
        db_session.execute(delete(model).where(model.stock_code == SENTINEL))
        db_session.commit()
    try:
        yield db_session
    finally:
        for model in (SaKlineDaily, SaAdjustFactor):
            db_session.execute(delete(model).where(model.stock_code == SENTINEL))
        db_session.commit()


def _bar(trade_date: date, close: float, pct: float | None) -> dict:
    return {
        "stock_code": SENTINEL,
        "trade_date": trade_date.isoformat(),
        "open": close,
        "close": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "volume": 1000,
        "amount": close * 1000,
        "pct_change": pct,
        "turnover": 0.5,
    }


def _days(n: int, base: date | None = None) -> list[date]:
    base = base or date(2026, 6, 1)
    return [base + timedelta(days=i) for i in range(n)]


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------

class TestFactorChain:
    def test_flat_series_constant_factor(self):
        """No corporate action → factor stays exactly 1.0 throughout."""
        days = _days(5)
        rows = [_bar(d, 100.0 + i, 1.0) for i, d in enumerate(days)]
        chain = sync_kline.compute_factor_chain(rows)
        assert all(f["adj_factor"] == 1.0 for f in chain)

    def test_dividend_day_jump(self):
        """Raw drops 5% on ex-div day but true pct is -1% → factor jumps once."""
        days = _days(4)
        closes = [100.0, 101.0, 96.0, 97.0]  # day3: raw -4.95%, true -1%
        raw_ret_day3 = 96.0 / 101.0
        true_ret_day3 = 0.99
        rows = [
            _bar(d, c, None if i == 0 else (true_ret_day3 - 1) * 100 if i == 2 else 1.0)
            for i, (d, c) in enumerate(zip(days, closes))
        ]
        chain = sync_kline.compute_factor_chain(rows)
        assert [f["adj_factor"] for f in chain[:2]] == [1.0, 1.0]
        expected_jump = true_ret_day3 / raw_ret_day3
        assert chain[2]["adj_factor"] == pytest.approx(expected_jump, rel=1e-9)
        # after the event the factor is constant again
        assert chain[3]["adj_factor"] == pytest.approx(expected_jump, rel=1e-9)

    def test_missing_pct_carries_forward(self):
        """Rows without pct (halt bars) keep the previous factor."""
        days = _days(3)
        rows = [_bar(d, 100.0, None) for d in days]
        chain = sync_kline.compute_factor_chain(rows)
        assert all(f["adj_factor"] == 1.0 for f in chain)

    def test_detect_factor_events(self):
        days = _days(4)
        rows = [
            _bar(days[0], 100.0, None),
            _bar(days[1], 101.0, 1.0),
            _bar(days[2], 96.0, -1.0),  # dividend
            _bar(days[3], 97.0, 1.04),
        ]
        assert sync_kline.detect_factor_events(rows) == [days[2]]

    def test_audit_helper_clean_vs_broken(self):
        """hfq close2close matches pct on a clean chain; flags a broken pct."""
        days = _days(4)
        # dividend on day2: raw 101→96 (-4.95%) true -1%
        rows = [
            _bar(days[0], 100.0, None),
            _bar(days[1], 101.0, 1.0),
            _bar(days[2], 96.0, -1.0),
            _bar(days[3], 97.0, 1.0416666667),  # 97/96*... consistent
        ]
        chain = sync_kline.compute_factor_chain(rows)
        assert sync_kline.hfq_close_2_close_deviation(rows, chain) == []

        # Now corrupt day3's pct: hfq return no longer matches → flagged.
        rows[3]["pct_change"] = 5.0
        bad = sync_kline.hfq_close_2_close_deviation(rows, chain)
        assert len(bad) == 1 and bad[0]["trade_date"] == days[3]


# ---------------------------------------------------------------------------
# DB round-trips (sentinel)
# ---------------------------------------------------------------------------

class TestKlineRoundTrip:
    def test_upsert_then_reupsert_idempotent(self, cleanup_sentinel):
        db = cleanup_sentinel
        days = _days(3)
        rows = [_bar(d, 100.0 + i, 1.0) for i, d in enumerate(days)]
        assert sync_kline.upsert_kline_rows(db, rows, source="tencent") == 3
        rows[0]["close"] = 111.0
        assert sync_kline.upsert_kline_rows(db, rows, source="tencent") == 3
        db.expire_all()
        bars = db.execute(
            select(SaKlineDaily).where(SaKlineDaily.stock_code == SENTINEL)
        ).scalars().all()
        assert len(bars) == 3
        assert sorted(float(b.close) for b in bars) == [101.0, 102.0, 111.0]
        assert all(b.source == "tencent" for b in bars)

    def test_init_adjust_factors_full_pass(self, cleanup_sentinel):
        db = cleanup_sentinel
        days = _days(4)
        rows = [
            _bar(days[0], 100.0, None),
            _bar(days[1], 101.0, 1.0),
            _bar(days[2], 96.0, -1.0),   # dividend: raw -4.95% vs true -1%
            _bar(days[3], 97.0, 1.04),
        ]
        sync_kline.upsert_kline_rows(db, rows)
        n = sync_kline.init_adjust_factors(db, SENTINEL)
        assert n == 4
        db.expire_all()
        factors = db.execute(
            select(SaAdjustFactor)
            .where(SaAdjustFactor.stock_code == SENTINEL)
            .order_by(SaAdjustFactor.trade_date.asc())
        ).scalars().all()
        assert float(factors[0].adj_factor) == 1.0
        expected = 0.99 / (96.0 / 101.0)
        assert float(factors[2].adj_factor) == pytest.approx(expected, rel=1e-6)
        assert factors[2].anchored == 1

    def test_maintain_incremental_detects_event_on_first_new_day(self, cleanup_sentinel):
        """Dividend lands on the FIRST new bar — the seed-close path must catch it."""
        db = cleanup_sentinel
        days = _days(4)
        base_rows = [
            _bar(days[0], 100.0, None),
            _bar(days[1], 101.0, 1.0),
            _bar(days[2], 102.0, 0.99),
        ]
        sync_kline.upsert_kline_rows(db, base_rows)
        sync_kline.init_adjust_factors(db, SENTINEL)

        # New day: raw 96.5 (-5.4%) but true -1% → corporate action.
        new_rows = [_bar(days[3], 96.5, -1.0)]
        event = sync_kline.maintain_factor_incremental(db, SENTINEL, new_rows)
        assert event is True

        db.expire_all()
        last = db.execute(
            select(SaAdjustFactor)
            .where(SaAdjustFactor.stock_code == SENTINEL)
            .order_by(SaAdjustFactor.trade_date.desc())
            .limit(1)
        ).scalar_one()
        assert last.trade_date == days[3]
        assert float(last.adj_factor) == pytest.approx(0.99 / (96.5 / 102.0), rel=1e-5)

    def test_sync_one_stock_v2_end_to_end(self, monkeypatch, cleanup_sentinel):
        """Mocked fetches → rows written, pct overlaid from qfq, factors built."""
        db = cleanup_sentinel
        days = _days(3)

        def fake_raw(symbol, start, end, max_bars=640):
            return [
                {
                    "stock_code": symbol, "trade_date": d.isoformat(),
                    "open": c, "close": c, "high": c, "low": c,
                    "volume": 100, "amount": c * 100,
                    "pct_change": 0.0,  # raw-side pct: WRONG on day2 (dividend)
                    "turnover": 0.1, "_source": "tencent",
                }
                for d, c in zip(days, [100.0, 101.0, 96.0])
            ]

        def fake_qfq(symbol, start, end, max_bars=640):
            # qfq says day2's true return is -1% (dividend day)
            return [
                {"stock_code": symbol, "trade_date": d.isoformat(), "pct_change": p}
                for d, p in zip(days, [None, 1.0, -1.0])
            ]

        monkeypatch.setattr(sync_kline.akshare_client, "fetch_daily_quotes_raw", fake_raw)
        monkeypatch.setattr(sync_kline.akshare_client, "fetch_daily_quotes", fake_qfq)

        written = sync_kline.sync_one_stock_v2(db, SENTINEL, "20260601", "20260603")
        assert written == 3
        db.expire_all()
        bars = db.execute(
            select(SaKlineDaily)
            .where(SaKlineDaily.stock_code == SENTINEL)
            .order_by(SaKlineDaily.trade_date.asc())
        ).scalars().all()
        # true pct overlaid on the dividend day
        assert float(bars[2].pct_change) == -1.0
