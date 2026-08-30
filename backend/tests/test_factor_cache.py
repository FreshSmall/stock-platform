"""Tests for the factor-layer session caches (app.factor.cache, N+1 fix part 2).

Query counts via the ORM ``do_orm_execute`` event; value equivalence against
direct SQL. Real DB, read-only, sentinel-free (only reads).
"""

from collections import namedtuple
from datetime import date

from sqlalchemy import event, select

from app.factor import cache as fcache
from app.factor import fundamental, sentiment
from app.models.stock import StockPool

_Row = namedtuple("_Row", ["trade_date", "value"])


def _counter(db):
    c = {"n": 0}

    def _incr(*_a, **_k):
        c["n"] += 1

    event.listen(db, "do_orm_execute", _incr)
    return c, lambda: event.remove(db, "do_orm_execute", _incr)


# --- pure: latest_le --------------------------------------------------------


def test_latest_le_picks_last_row_within_date():
    rows = [_Row(date(2026, 1, 5), 1.0), _Row(date(2026, 2, 10), 2.0), _Row(date(2026, 3, 15), 3.0)]
    # exact / between / before-all / after-all
    assert fcache.latest_le(rows, date(2026, 2, 10)).value == 2.0
    assert fcache.latest_le(rows, date(2026, 2, 20)).value == 2.0
    assert fcache.latest_le(rows, date(2026, 1, 1)) is None
    assert fcache.latest_le(rows, date(2026, 12, 31)).value == 3.0
    assert fcache.latest_le([], date(2026, 1, 1)) is None


# --- stock_pool cached lookups ----------------------------------------------


def _pool_code(db) -> tuple[str, date]:
    """A code with a stock_pool snapshot and its snapshot date."""
    row = db.execute(
        select(StockPool.stock_code, StockPool.trade_date)
        .order_by(StockPool.trade_date.desc())
        .limit(1)
    ).one()
    return row.stock_code, row.trade_date


def test_pool_field_matches_direct_sql_and_queries_once(db_session):
    code, snap_date = _pool_code(db_session)
    direct = db_session.execute(
        select(StockPool.pe)
        .where(StockPool.stock_code == code, StockPool.trade_date <= snap_date)
        .order_by(StockPool.trade_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    expected = float(direct) if direct is not None else None

    counter, unsubscribe = _counter(db_session)
    try:
        v1 = fundamental.PeFactor().compute(db_session, code, snap_date)
        v2 = fundamental.PeFactor().compute(db_session, code, snap_date)  # cached
        assert counter["n"] == 1  # one per-code load, second lookup in memory
        assert v1 == v2 == expected
    finally:
        unsubscribe()


def test_prefetch_serves_pool_lookups_without_queries(db_session):
    code, snap_date = _pool_code(db_session)
    fcache.prefetch_stock_pool(db_session, [code], snap_date)
    counter, unsubscribe = _counter(db_session)
    try:
        v = fundamental.PeFactor().compute(db_session, code, snap_date)
        assert counter["n"] == 0
        assert v is None or isinstance(v, float)
    finally:
        unsubscribe()


# --- sentiment market tables ------------------------------------------------


def test_market_sentiment_cached_across_codes(db_session):
    """IC pattern: 300 codes × the same market date → one table load."""
    counter, unsubscribe = _counter(db_session)
    try:
        vals = [
            sentiment.LimitUpCountFactor().compute(db_session, c, date(2026, 8, 19))
            for c in ("600519", "000858", "601318")
        ]
        assert counter["n"] <= 1  # whole-table load once (table may be empty)
        assert all(v is None or isinstance(v, float) for v in vals)
    finally:
        unsubscribe()


def test_north_flow_uses_daily_aggregate_cache(db_session):
    counter, unsubscribe = _counter(db_session)
    try:
        for _ in range(3):
            sentiment.NorthFlowFactor().compute(db_session, "600519", date(2026, 8, 19))
        assert counter["n"] <= 1
    finally:
        unsubscribe()
