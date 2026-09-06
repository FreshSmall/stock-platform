"""Tests for the shared execution layer (V3a BP-V3a-003 prerequisite).

Pure-function coverage for the matching helpers extracted from the portfolio
backtest: slipped fill prices, board-lot sizing, the fee breakdown's
consistency with ``cost_model``, and the buy-skip / sell-defer decision.
The two DB lookups get one smoke test on the real database (read-only, so
there is nothing to clean up afterwards).
"""

import pandas as pd
import pytest

from app.services.cost_model import CostParams
from app.services.execution import (
    board_lot_shares,
    cost_breakdown,
    fetch_open_prices,
    fetch_tradability,
    fill_price,
    match_fill,
)


# --- fill price ---------------------------------------------------------------


def test_fill_price_direction():
    # buy pays up / sell receives less, on the same slippage basis
    assert fill_price(10.0, "buy", 0.001) == pytest.approx(10.0 * (1.0 + 0.001))
    assert fill_price(10.0, "sell", 0.001) == pytest.approx(10.0 * (1.0 - 0.001))
    assert fill_price(10.0, "buy", 0.0) == pytest.approx(10.0)
    assert fill_price(1302.8, "sell", 0.001) == pytest.approx(1302.8 * 0.999)
    with pytest.raises(ValueError, match="side"):
        fill_price(10.0, "hold", 0.001)


# --- board lot ----------------------------------------------------------------


def test_board_lot_floors_to_whole_lots():
    # exactly one affordable lot
    assert board_lot_shares(1_000.0, 10.0) == 100
    # fractional lots are floored: 2350 @ 7.8 → 3.01 lots → 300 shares
    assert board_lot_shares(2_350.0, 7.8) == 300
    assert board_lot_shares(100_000.0, 50.0) == 2_000
    # not enough for a single lot → 0
    assert board_lot_shares(999.9, 10.0) == 0
    assert board_lot_shares(0.0, 10.0) == 0


def test_board_lot_invalid_price_yields_zero():
    for bad_price in (0.0, -3.0, float("nan"), float("inf"), None):
        assert board_lot_shares(10_000.0, bad_price) == 0


# --- cost breakdown -----------------------------------------------------------


def test_cost_breakdown_matches_cost_model_totals():
    cp = CostParams()
    for side in ("buy", "sell"):
        for amount in (1_000.0, 50_000.0, 1_000_000.0):
            b = cost_breakdown(side, amount, cp)
            ref = cp.sell_cost(amount) if side == "sell" else cp.buy_cost(amount)
            assert b["total"] == pytest.approx(ref)
            # the itemization itself must add up to the same total
            assert (
                b["commission"] + b["stamp_duty"] + b["transfer_fee"]
                == pytest.approx(ref)
            )


def test_cost_breakdown_directional_fees():
    cp = CostParams()
    amount = 1_000_000.0
    buy = cost_breakdown("buy", amount, cp)
    sell = cost_breakdown("sell", amount, cp)
    # stamp duty lands on sells only
    assert buy["stamp_duty"] == 0.0
    assert sell["stamp_duty"] == pytest.approx(amount * cp.stamp_duty_rate)
    # transfer fee is charged on both sides
    for b in (buy, sell):
        assert b["transfer_fee"] == pytest.approx(amount * cp.transfer_fee_rate)
    # small ticket: the min-commission floor (5 CNY) dominates
    tiny = cost_breakdown("buy", 1_000.0, cp)
    assert tiny["commission"] == pytest.approx(cp.min_commission)
    big = cost_breakdown("buy", amount, cp)
    assert big["commission"] == pytest.approx(amount * cp.commission_rate)
    with pytest.raises(ValueError, match="side"):
        cost_breakdown("hold", 1_000.0, cp)


# --- fill decision ------------------------------------------------------------


def test_match_fill_three_branches():
    # fillable → fill with no reason
    assert match_fill("buy", 10.0, True) == ("fill", "")
    assert match_fill("sell", 10.0, True) == ("fill", "")
    # blocked: a buy is skipped this cycle, a sell is deferred (carried)
    assert match_fill("buy", 10.0, False) == ("skip", "buy_blocked")
    assert match_fill("sell", 10.0, False) == ("defer", "sell_blocked")
    # missing/invalid open price: same buy-skip / sell-defer split
    assert match_fill("buy", None, True) == ("skip", "no_open_price")
    assert match_fill("sell", None, True) == ("defer", "no_open_price")
    assert match_fill("buy", 0.0, True) == ("skip", "no_open_price")
    with pytest.raises(ValueError, match="side"):
        match_fill("hold", 10.0, True)


# --- DB smoke (real database, read-only) --------------------------------------


def test_fetch_open_prices_and_tradability_smoke(db_session):
    d = pd.Timestamp("2026-09-02")  # a trade date present in the local dataset
    codes = ["000545", "600519"]
    opens = fetch_open_prices(db_session, codes, d)
    assert opens  # both codes quote on that date
    assert all(v > 0 for v in opens.values())

    sell_ok, buy_ok = fetch_tradability(db_session, codes, d)
    assert all(isinstance(v, bool) for v in sell_ok.values())
    assert all(isinstance(v, bool) for v in buy_ok.values())

    # missing rows are permissive: an unknown code is simply absent from the
    # maps (callers resolve with .get(code, True))
    assert fetch_open_prices(db_session, ["NO_SUCH_CODE"], d) == {}
    assert fetch_tradability(db_session, ["NO_SUCH_CODE"], d) == ({}, {})

    # empty universe → empty maps
    assert fetch_open_prices(db_session, [], d) == {}
    assert fetch_tradability(db_session, [], d) == ({}, {})
