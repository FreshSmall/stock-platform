"""Tests for the V3a paper-trading pure analytics layer.

Hand-computed fixtures only — no DB, no pandas: rebalance calendar
semantics (incl. the ISO week spanning New Year), order-intent diffing
(lot rounding, buy buffer, missing prices), return attribution, weekly
paper-vs-backtest drift, holding overlap and alert triage.
"""

from datetime import date

import pytest

from app.services.paper_analytics import (
    OrderIntent,
    alert_eval,
    attribution,
    diff_orders,
    drift_compare,
    holding_jaccard,
    rebalance_dates,
)


# --- rebalance_dates ---------------------------------------------------------


def test_rebalance_dates_weekly_iso_weeks_cross_new_year():
    # 2026-12-28..2027-01-03 is ONE ISO week (2026-W53): calendar-year logic
    # would pick 2026-12-31, ISO-week logic picks 2027-01-01.
    dates = [
        date(2026, 12, 28),  # Mon, 2026-W53
        date(2026, 12, 29),
        date(2026, 12, 30),
        date(2026, 12, 31),
        date(2027, 1, 1),  # Fri, still 2026-W53
        date(2027, 1, 4),  # Mon, 2027-W01
        date(2027, 1, 5),
    ]
    assert rebalance_dates(dates, "W") == [date(2027, 1, 1), date(2027, 1, 5)]


def test_rebalance_dates_monthly():
    dates = [
        date(2026, 1, 28),
        date(2026, 1, 29),
        date(2026, 2, 2),
        date(2026, 2, 27),
        date(2026, 3, 2),
        date(2026, 3, 31),
    ]
    assert rebalance_dates(dates, "M") == [
        date(2026, 1, 29),
        date(2026, 2, 27),
        date(2026, 3, 31),
    ]


def test_rebalance_dates_every_n_trade_days():
    dates = [date(2026, 9, d) for d in range(1, 11)]  # 10 trade days
    assert rebalance_dates(dates, "3") == [
        date(2026, 9, 1),
        date(2026, 9, 4),
        date(2026, 9, 7),
        date(2026, 9, 10),
    ]
    assert rebalance_dates(dates, "1") == dates
    assert rebalance_dates([], "W") == []


def test_rebalance_dates_invalid_freq():
    with pytest.raises(ValueError, match="freq 必须是"):
        rebalance_dates([date(2026, 9, 1)], "X")
    with pytest.raises(ValueError, match="间距必须"):
        rebalance_dates([date(2026, 9, 1)], "0")


# --- diff_orders -------------------------------------------------------------


def test_diff_orders_full_sell_new_buys_lot_buffer_and_missing_price():
    # total assets = 200×10 market value + 8000 cash = 10000
    current = {"000001": 200}
    target = {"600519": 0.6, "000002": 0.3, "600036": 0.1}
    # 000002 suspended → no last price; everything else priced
    last_price = {"000001": 10.0, "600519": 10.0, "600036": 5.0}
    orders = diff_orders(current, target, last_price, 8000.0)
    # held-but-not-in-target → sell all 200; buys: 2% buffer + 100-share lots
    #   600519: 0.6×10000×0.98/10 = 588 → floor to 500 shares
    #   600036: 0.1×10000×0.98/5  = 196 → floor to 100 shares
    #   000002: no price → 0 shares
    # sells first, then buys by stock_code
    assert orders == [
        OrderIntent("000001", "sell", 0.0, 200),
        OrderIntent("000002", "buy", 0.3, 0),
        OrderIntent("600036", "buy", 0.1, 100),
        OrderIntent("600519", "buy", 0.6, 500),
    ]


def test_diff_orders_within_tolerance_untouched_overweight_adjusts():
    # total = 10000 + 5000 + 10000 + 25000 cash = 50000
    current = {"600000": 100, "600036": 500, "000001": 100}
    target = {"600000": 0.5, "600036": 0.1, "000001": 0.4}
    last_price = {"600000": 100.0, "600036": 10.0, "000001": 100.0}
    orders = diff_orders(current, target, last_price, 25000.0)
    # 600000: actual 0.2 vs 0.5 → gap 0.3 > 0.2 → buy 0.5×50000×0.98/100
    #         = 245 shares → floor to 200
    # 600036: actual 0.1 = target → untouched
    # 000001: actual 0.2 vs 0.4 → gap exactly 0.2 ≤ tol → untouched
    assert orders == [OrderIntent("600000", "buy", 0.5, 200)]


def test_diff_orders_overweight_in_target_sells_entire_holding():
    # total = 5000 + 10000 = 15000; 600036 actual 1/3 vs target 0.1 → sell
    # 600000 actual 2/3 vs 0.5 → gap 0.1667 ≤ 0.2 → untouched
    current = {"600036": 500, "600000": 100}
    target = {"600036": 0.1, "600000": 0.5}
    last_price = {"600036": 10.0, "600000": 100.0}
    orders = diff_orders(current, target, last_price, 0.0)
    assert orders == [OrderIntent("600036", "sell", 0.1, 500)]


def test_diff_orders_priceless_holding_counts_zero_value_but_still_sells():
    current = {"600519": 100, "000001": 100}
    target = {"000001": 1.0}
    last_price = {"000001": 10.0}  # 600519 suspended → no price, 0 market value
    orders = diff_orders(current, target, last_price, 0.0)
    # 000001 actual weight = 1000/1000 = 1.0 = target → untouched
    assert orders == [OrderIntent("600519", "sell", 0.0, 100)]


# --- attribution -------------------------------------------------------------


def test_attribution_hand_computed_five_days():
    nav = [1.00, 1.02, 1.03, 1.04, 1.05]
    bench = [1.00, 1.01, 1.02, 1.03, 1.04]
    cash_weights = [0.5, 0.1, 0.2, 0.1, 0.0]  # day-0 weight not counted
    out = attribution(nav, bench, 60.0, cash_weights, 100_000.0)
    assert out["excess"] == pytest.approx((1.05 / 1.00) - (1.04 / 1.00))
    assert out["cost_drag"] == pytest.approx(-60.0 / 100_000.0)
    # bench daily returns from day 1 on: 1%, 1.02/1.01, 1.03/1.02, 1.04/1.03
    expected_cash_drag = (
        0.1 * 0.01
        + 0.2 * (1.02 / 1.01 - 1)
        + 0.1 * (1.03 / 1.02 - 1)
        + 0.0 * (1.04 / 1.03 - 1)
    )
    assert out["cash_drag"] == pytest.approx(expected_cash_drag)
    assert out["selection"] == pytest.approx(
        (1.05 - 1.04) - (-60.0 / 100_000.0) - expected_cash_drag
    )


def test_attribution_empty_or_mismatched_inputs_return_all_zeros():
    zeros = {"excess": 0.0, "cost_drag": 0.0, "cash_drag": 0.0, "selection": 0.0}
    assert attribution([], [], 10.0, [], 1000.0) == zeros
    # bench shorter than nav
    assert attribution([1.0, 1.1], [1.0], 0.0, [0.0, 0.0], 1000.0) == zeros
    # cash_weights shorter than nav
    assert attribution([1.0, 1.1], [1.0, 1.05], 0.0, [0.0], 1000.0) == zeros


# --- drift_compare -----------------------------------------------------------


def test_drift_compare_two_weeks_hand_computed():
    week1 = [
        date(2026, 9, 7),  # Mon, 2026-W37
        date(2026, 9, 8),
        date(2026, 9, 9),
        date(2026, 9, 10),
        date(2026, 9, 11),
    ]
    paper = list(zip(week1, [1.00, 1.01, 1.02, 1.01, 1.03]))
    paper += [
        (date(2026, 9, 14), 1.05),  # Mon, 2026-W38
        (date(2026, 9, 15), 1.04),
        (date(2026, 9, 16), 1.10),  # paper-only date → excluded from alignment
    ]
    backtest = list(zip(week1, [1.00, 1.02, 1.02, 1.02, 1.04]))
    backtest += [
        (date(2026, 9, 14), 1.06),
        (date(2026, 9, 15), 1.07),
        (date(2026, 9, 18), 1.09),  # backtest-only date → excluded
    ]
    out = drift_compare(paper, backtest)
    w1, w2 = out["weekly"]
    # W37: paper 1.03/1.00−1 = +3%; backtest 1.04/1.00−1 = +4%
    assert w1["week"] == "2026-W37"
    assert w1["paper_ret"] == pytest.approx(0.03)
    assert w1["backtest_ret"] == pytest.approx(0.04)
    assert w1["diff"] == pytest.approx(-0.01)
    # W38 aligned on 09-14..09-15 only
    assert w2["week"] == "2026-W38"
    assert w2["paper_ret"] == pytest.approx(1.04 / 1.05 - 1)
    assert w2["backtest_ret"] == pytest.approx(1.07 / 1.06 - 1)
    assert w2["diff"] == pytest.approx((1.04 / 1.05 - 1) - (1.07 / 1.06 - 1))
    # cumulative over the whole aligned interval (09-07 → 09-15)
    assert out["cum_paper"] == pytest.approx(1.04 / 1.00 - 1)
    assert out["cum_backtest"] == pytest.approx(1.07 / 1.00 - 1)
    assert out["max_abs_weekly"] == pytest.approx(
        abs((1.04 / 1.05 - 1) - (1.07 / 1.06 - 1))
    )


def test_drift_compare_fewer_than_two_weeks_is_empty_not_an_error():
    one_week = [(date(2026, 9, 7), 1.0), (date(2026, 9, 10), 1.1)]
    out = drift_compare(one_week, [(date(2026, 9, 7), 1.0), (date(2026, 9, 10), 1.05)])
    assert out["weekly"] == []
    assert out["cum_paper"] == pytest.approx(0.1)
    assert out["cum_backtest"] == pytest.approx(0.05)
    assert out["max_abs_weekly"] == 0.0
    # disjoint dates → empty intersection, everything zero, still no error
    disjoint = drift_compare([(date(2026, 9, 7), 1.0)], [(date(2026, 9, 14), 1.0)])
    assert disjoint == {
        "weekly": [],
        "cum_paper": 0.0,
        "cum_backtest": 0.0,
        "max_abs_weekly": 0.0,
    }


# --- holding overlap / alert triage ------------------------------------------


def test_holding_jaccard_including_both_empty():
    assert holding_jaccard({"a", "b", "c"}, {"b", "c", "d"}) == pytest.approx(0.5)
    assert holding_jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert holding_jaccard(set(), set()) == 1.0  # 双空集视为完全一致
    assert holding_jaccard({"a"}, set()) == 0.0


def test_alert_eval_all_branches():
    # upper-limit semantics (default): exceed → bad
    assert alert_eval(0.05, warn=0.10, fail=0.20) == "pass"
    assert alert_eval(0.10, warn=0.10, fail=0.20) == "pass"  # 恰好等于阈值
    assert alert_eval(0.15, warn=0.10, fail=0.20) == "warn"
    assert alert_eval(0.25, warn=0.10, fail=0.20) == "fail"
    # lower-is-bad (floor semantics): e.g. minimum cash ratio
    assert alert_eval(1500.0, warn=1000.0, fail=100.0, lower_is_bad=True) == "pass"
    assert alert_eval(500.0, warn=1000.0, fail=100.0, lower_is_bad=True) == "warn"
    assert alert_eval(50.0, warn=1000.0, fail=100.0, lower_is_bad=True) == "fail"
    assert alert_eval(1000.0, warn=1000.0, fail=100.0, lower_is_bad=True) == "pass"
    # missing metric cannot be judged
    assert alert_eval(None, warn=0.10, fail=0.20) == "warn"
