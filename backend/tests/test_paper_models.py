"""Tests for the V3a paper-trading model layer (T3.1 / BP-V3a-001).

Runs against the real MySQL database via the ``db_session`` fixture. Every
test inserts rows under a uuid-tagged account name and deletes exactly those
rows (children first) before finishing — no residue, no other table touched.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.models.paper import (
    SaPaperAccount,
    SaPaperNav,
    SaPaperOrder,
    SaPaperPosition,
)


def _insert_account(db_session) -> SaPaperAccount:
    """Insert one uniquely-named paper account and commit it."""
    account = SaPaperAccount(
        name=f"paper_test_{uuid.uuid4().hex[:8]}",
        config={"strategy": "equal_weight", "top_n": 3},
        initial_cash=Decimal("1000000.00"),
        cash=Decimal("1000000.00"),
    )
    db_session.add(account)
    db_session.commit()
    return account


def _cleanup_account(db_session, account_id: int) -> None:
    """Delete every row this test created: children first, then the account."""
    db_session.execute(delete(SaPaperNav).where(SaPaperNav.account_id == account_id))
    db_session.execute(
        delete(SaPaperPosition).where(SaPaperPosition.account_id == account_id)
    )
    db_session.execute(delete(SaPaperOrder).where(SaPaperOrder.account_id == account_id))
    db_session.execute(delete(SaPaperAccount).where(SaPaperAccount.id == account_id))
    db_session.commit()


def test_account_insert_query_and_unique_name(db_session):
    account = _insert_account(db_session)
    try:
        fetched = db_session.execute(
            select(SaPaperAccount).where(SaPaperAccount.id == account.id)
        ).scalar_one()
        assert fetched.name == account.name
        assert fetched.status == "active"  # server default applied
        assert fetched.config == {"strategy": "equal_weight", "top_n": 3}
        assert fetched.cash == Decimal("1000000.00")

        dup = SaPaperAccount(
            name=account.name,  # same name must trip uk_name
            config={},
            initial_cash=Decimal("1.00"),
            cash=Decimal("1.00"),
        )
        db_session.add(dup)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()
    finally:
        _cleanup_account(db_session, account.id)


def test_order_unique_per_account_signal_stock_side(db_session):
    account = _insert_account(db_session)
    try:
        signal = date(2026, 9, 1)
        first = SaPaperOrder(
            account_id=account.id,
            signal_date=signal,
            exec_date=signal + timedelta(days=1),
            stock_code="600519",
            side="buy",
            target_weight=Decimal("0.200000"),
        )
        db_session.add(first)
        db_session.commit()

        dup = SaPaperOrder(
            account_id=account.id,
            signal_date=signal,
            exec_date=signal + timedelta(days=1),
            stock_code="600519",
            side="buy",  # same (account, signal_date, stock, side) -> UK hit
        )
        db_session.add(dup)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

        sell = SaPaperOrder(
            account_id=account.id,
            signal_date=signal,
            exec_date=signal + timedelta(days=1),
            stock_code="600519",
            side="sell",  # different side is a distinct intent -> allowed
        )
        db_session.add(sell)
        db_session.commit()
    finally:
        _cleanup_account(db_session, account.id)


def test_position_unique_per_account_date_code(db_session):
    account = _insert_account(db_session)
    try:
        day = date(2026, 9, 2)
        db_session.add(
            SaPaperPosition(
                account_id=account.id,
                trade_date=day,
                stock_code="600519",
                shares=100,
                close_price=Decimal("1700.0000"),
                market_value=Decimal("170000.00"),
            )
        )
        db_session.commit()

        db_session.add(
            SaPaperPosition(
                account_id=account.id,
                trade_date=day,
                stock_code="600519",  # same (account, date, stock) -> UK hit
                shares=200,
                close_price=Decimal("1700.0000"),
                market_value=Decimal("340000.00"),
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()
    finally:
        _cleanup_account(db_session, account.id)


def test_nav_unique_per_account_date(db_session):
    account = _insert_account(db_session)
    try:
        day = date(2026, 9, 2)
        db_session.add(
            SaPaperNav(
                account_id=account.id,
                trade_date=day,
                cash=Decimal("830000.00"),
                market_value=Decimal("170000.00"),
                total_asset=Decimal("1000000.00"),
                nav=Decimal("1.000000"),
            )
        )
        db_session.commit()

        db_session.add(
            SaPaperNav(
                account_id=account.id,
                trade_date=day,  # same (account, date) -> UK hit
                cash=Decimal("0.00"),
                market_value=Decimal("0.00"),
                total_asset=Decimal("0.00"),
                nav=Decimal("0.000000"),
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()
    finally:
        _cleanup_account(db_session, account.id)
