"""Integration tests for the SQLAlchemy database layer.

These tests run against the REAL ``stock_analysis`` database and assert that
the connection works and that the ORM mappings line up with the actual table
schemas. They do not write to the database (the mapped tables are read-only).
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import select, text

from app.models.stock import DailyPrice, StockPool


def test_can_connect_and_query_daily_prices(db_session) -> None:
    """A plain SQL query for 贵州茅台 (600519) returns a row with a positive close."""
    row = db_session.execute(
        text(
            "SELECT close FROM daily_prices "
            "WHERE stock_code = :code "
            "ORDER BY trade_date DESC LIMIT 1"
        ),
        {"code": "600519"},
    ).first()

    assert row is not None, "expected at least one row for stock_code='600519'"
    close = row[0]
    assert isinstance(close, Decimal)
    assert close > 0


def test_stock_pool_has_records(db_session) -> None:
    """The ``stock_pool`` table contains at least one row."""
    row = db_session.execute(text("SELECT * FROM stock_pool LIMIT 1")).first()
    assert row is not None


def test_orm_daily_price_mapping_fields(db_session) -> None:
    """The DailyPrice ORM mapping hydrates a row and exposes ``.close``."""
    stmt = (
        select(DailyPrice)
        .where(DailyPrice.stock_code == "600519")
        .order_by(DailyPrice.trade_date.desc())
        .limit(1)
    )
    result = db_session.execute(stmt).scalar_one()

    assert result is not None
    # The most recent trade_date for 600519 must be a real date.
    assert isinstance(result.trade_date, date)
    assert result.close is not None
    assert result.close > 0


def test_orm_stock_pool_mapping_fields(db_session) -> None:
    """The StockPool ORM mapping hydrates a row and exposes its typed fields."""
    result = db_session.execute(
        select(StockPool).order_by(StockPool.id.asc()).limit(1)
    ).scalar_one()

    assert result is not None
    assert result.pool_name
    assert result.stock_code
    assert isinstance(result.trade_date, date)
