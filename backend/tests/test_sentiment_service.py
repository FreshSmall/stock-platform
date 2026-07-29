"""Tests for V1.5 sentiment_service: limit-up classification + streak + rollup.

Two layers:
  - Pure-function tests for the limit rule (no DB) — fast, exhaustive.
  - DB-backed tests for compute_streak/compute_sentiment using a sentinel code
    that can never collide with a real A-share (safety convention).
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.models.sentiment import SaLimitUpStreak, SaMarketSentiment
from app.models.stock import DailyPrice
from app.services import sentiment_service as ss

SENTINEL = "ZZSNT"
SENTINEL2 = "ZZSN2"


# --- pure-function: limit rule --------------------------------------------


def test_threshold_by_board():
    assert ss.limit_threshold("600519", "贵州茅台") == Decimal("0.10")
    assert ss.limit_threshold("000001", "平安银行") == Decimal("0.10")
    assert ss.limit_threshold("300750", "宁德时代") == Decimal("0.20")
    assert ss.limit_threshold("688981", "中芯国际") == Decimal("0.20")


def test_threshold_st_overrides():
    assert ss.limit_threshold("600519", "ST茅台") == Decimal("0.05")
    assert ss.limit_threshold("300750", "*ST宁德") == Decimal("0.05")


def test_limit_prices_rounds_to_fen():
    up, down = ss.limit_prices(Decimal("10.00"), Decimal("0.10"))
    assert up == Decimal("11.00")
    assert down == Decimal("9.00")
    # rounding: 10.03 * 1.1 = 11.033 -> 11.03
    up2, _ = ss.limit_prices(Decimal("10.03"), Decimal("0.10"))
    assert up2 == Decimal("11.03")


@pytest.mark.parametrize(
    "prev,close,high,threshold,expected",
    [
        (10.00, 11.00, 11.00, "0.10", "limit_up"),     # sealed at up limit
        (10.00, 10.50, 11.00, "0.10", "failed_limit"), # touched but not sealed
        (10.00, 9.00, 10.00, "0.10", "limit_down"),    # sealed at down limit
        (10.00, 10.50, 10.50, "0.10", "normal"),       # mid-range
        (10.00, 11.00, 11.00, "0.20", "normal"),       # 10% move, 20% board -> not limit
        (None, 11.00, 11.00, "0.10", "normal"),        # missing prev close
    ],
)
def test_classify(prev, close, high, threshold, expected):
    p = Decimal(str(prev)) if prev is not None else None
    c = Decimal(str(close)) if close is not None else None
    h = Decimal(str(high)) if high is not None else None
    assert ss.classify(p, c, h, Decimal(threshold)) == expected


# --- DB-backed: streak + sentiment ----------------------------------------


@pytest.fixture
def with_two_days(db_session):
    """Insert two synthetic trading days (prev + today) for two sentinel codes.

    Cleans up both daily_prices and any sentiment/streak rows afterward.
    """
    prev = date(2026, 7, 27)
    today = date(2026, 7, 28)

    def _insert(code, d, close, high, pct):
        db_session.add(
            DailyPrice(
                stock_code=code, trade_date=d, open=close, close=Decimal(str(close)),
                high=Decimal(str(high)), low=Decimal(str(close)),
                volume=1000, amount=Decimal(str(close * 1000)),
                pct_change=Decimal(str(pct)),
            )
        )

    # prev close 10.00 for both; today code1 seals +10% (limit_up, board 10%),
    # code2 touches but closes +5% (failed_limit).
    _insert(SENTINEL, prev, 10.00, 10.00, 0)
    _insert(SENTINEL2, prev, 10.00, 10.00, 0)
    _insert(SENTINEL, today, 11.00, 11.00, 10.0)   # limit_up
    _insert(SENTINEL2, today, 10.50, 11.00, 5.0)   # failed_limit
    db_session.commit()

    try:
        yield {"prev": prev, "today": today}
    finally:
        db_session.execute(delete(DailyPrice).where(DailyPrice.stock_code.in_([SENTINEL, SENTINEL2])))
        db_session.execute(delete(SaLimitUpStreak).where(SaLimitUpStreak.stock_code.in_([SENTINEL, SENTINEL2])))
        db_session.execute(delete(SaMarketSentiment))
        db_session.commit()


def test_compute_streak_increments(with_two_days, db_session):
    today = with_two_days["today"]
    streaks = ss.compute_streak(db_session, today)
    assert streaks[SENTINEL] == 1   # first limit-up -> streak 1
    assert streaks[SENTINEL2] == 0  # failed limit -> streak 0


def test_compute_streak_rolls_over(with_two_days, db_session):
    """A second consecutive limit-up day must increment to 2; a break resets to 0."""
    today = with_two_days["today"]
    ss.compute_streak(db_session, today)  # day1: streak 1

    # add day2: code1 limits again (streak 2), code2 does NOT limit (streak 0)
    day2 = date(2026, 7, 29)
    db_session.add(DailyPrice(
        stock_code=SENTINEL, trade_date=day2, close=Decimal("12.10"),
        high=Decimal("12.10"), open=Decimal("11.00"), low=Decimal("11.00"),
        volume=1, amount=Decimal("12"), pct_change=Decimal("10.0"),
    ))
    db_session.add(DailyPrice(
        stock_code=SENTINEL2, trade_date=day2, close=Decimal("10.60"),
        high=Decimal("10.80"), open=Decimal("10.50"), low=Decimal("10.50"),
        volume=1, amount=Decimal("10"), pct_change=Decimal("0.95"),
    ))
    db_session.commit()
    try:
        streaks = ss.compute_streak(db_session, day2)
        assert streaks[SENTINEL] == 2
        assert streaks[SENTINEL2] == 0
    finally:
        db_session.execute(delete(DailyPrice).where(DailyPrice.trade_date == day2))
        db_session.execute(delete(SaLimitUpStreak).where(SaLimitUpStreak.trade_date == day2))
        db_session.commit()


def test_compute_sentiment_rollup(with_two_days, db_session):
    today = with_two_days["today"]
    # NOTE: compute_sentiment counts across ALL daily_prices for `today`, which
    # includes real market data. We assert on the sentinel contributions by
    # checking the rollup is non-null and the streak ladder >= our cases.
    data = ss.compute_sentiment(db_session, today)
    assert data is not None
    assert data["limit_up_count"] >= 1
    assert data["failed_limit_count"] >= 1
    # seal_rate = sealed / (sealed + failed)
    assert data["seal_rate"] is not None
    assert data["max_streak"] >= 1
    assert data["streak_ladder"] is not None
    assert "1" in data["streak_ladder"]  # at least one 1-board stock


def test_compute_sentiment_no_data_returns_none(db_session):
    # a date with zero daily_prices rows -> None
    assert ss.compute_sentiment(db_session, date(2099, 1, 1)) is None
