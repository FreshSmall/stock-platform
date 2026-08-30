"""Tests for the session-scoped kline cache (factor N+1 fix).

Query counts are asserted via the ORM ``do_orm_execute`` event on the real
session — these tests hit the real DB read-only, like the rest of the suite.
"""

from datetime import timedelta

from sqlalchemy import event, select

from app.models.stock import DailyPrice
from app.services import market_service


def _query_counter(db):
    counter = {"n": 0}

    def _incr(*_a, **_k):
        counter["n"] += 1

    event.listen(db, "do_orm_execute", _incr)
    return counter, lambda: event.remove(db, "do_orm_execute", _incr)


def _a_deep_code(db) -> str:
    """A code with plenty of history (post-back-fill there are ~1200 bars)."""
    code = db.execute(select(DailyPrice.stock_code).limit(1)).scalars().first()
    assert code, "daily_prices is empty"
    return code


def test_repeated_daily_kline_calls_query_once(db_session):
    """The compute_series loop pattern: same code, per-day end dates."""
    code = _a_deep_code(db_session)
    counter, unsubscribe = _query_counter(db_session)
    try:
        full = market_service.get_kline(db_session, code)
        assert len(full) > 100  # deep history thanks to the back-fill
        # per-day slices like the factor computes issue
        partial = market_service.get_kline(db_session, code, end=full[-1].trade_date)
        older = market_service.get_kline(db_session, code, end=full[10].trade_date)
        assert counter["n"] == 1  # one full load, everything else in memory
        assert [r.trade_date for r in older] == [r.trade_date for r in full[:11]]
        assert len(partial) == len(full)
    finally:
        unsubscribe()


def test_prefetch_serves_universe_without_per_code_queries(db_session):
    """The compute_ic pattern: many codes, windowed bulk prefetch."""
    code = _a_deep_code(db_session)
    end = db_session.execute(
        select(DailyPrice.trade_date).where(DailyPrice.stock_code == code)
        .order_by(DailyPrice.trade_date.desc()).limit(1)
    ).scalar()

    market_service.prefetch_kline_windows(db_session, [code], end, calendar_days=200)

    counter, unsubscribe = _query_counter(db_session)
    try:
        rows = market_service.get_kline(db_session, code, end=end)
        assert counter["n"] == 0  # served entirely from the prefetch
        assert rows and all(r.trade_date <= end for r in rows)
        assert all(r.trade_date >= end - timedelta(days=200) for r in rows)
    finally:
        unsubscribe()


def test_window_beyond_prefetch_falls_back_to_query(db_session):
    """A request outside the prefetched bounds must not be served stale."""
    code = _a_deep_code(db_session)
    end = db_session.execute(
        select(DailyPrice.trade_date).where(DailyPrice.stock_code == code)
        .order_by(DailyPrice.trade_date.desc()).limit(1)
    ).scalar()
    market_service.prefetch_kline_windows(db_session, [code], end, calendar_days=200)

    counter, unsubscribe = _query_counter(db_session)
    try:
        # needs history older than the prefetched window → full load
        rows = market_service.get_kline(
            db_session, code, start=end - timedelta(days=600)
        )
        assert counter["n"] == 1
        assert len(rows) > 200
    finally:
        unsubscribe()
