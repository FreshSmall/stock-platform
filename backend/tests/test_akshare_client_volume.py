"""Unit tests for the eastmoney→手 volume normalization in akshare_client.

The eastmoney fallback (``ak.stock_zh_a_hist`` / ``stock_zh_a_hist_min_em``)
returns ``成交量`` in 股 (shares), while the Tencent primary source returns 手
(1 手 = 100 股). ``_shares_to_lots`` normalizes eastmoney to 手 so both sources
land in the same unit — without it, a stock's volume can jump 100× between
consecutive days depending on which source won.

These tests pin the divisor and the None/zero/string edge cases so a future
refactor can't silently drop the ÷100.
"""

import math

from app.data.akshare_client import _shares_to_lots


def test_shares_to_lots_divides_by_100() -> None:
    """A whole-lot share count divides cleanly to lots."""
    # 580,823,797 股 (ICBC 2026-07-28) → 5,808,237 手
    assert _shares_to_lots(580823797) == 5808237


def test_shares_to_lots_accepts_numeric_string() -> None:
    """akshare/pandas may hand us a string like '580823797.0'."""
    assert _shares_to_lots("580823797.0") == 5808237


def test_shares_to_lots_none_passthrough() -> None:
    """Missing volume stays missing (no spurious 0)."""
    assert _shares_to_lots(None) is None


def test_shares_to_lots_zero() -> None:
    """Zero shares → zero lots (suspended-day row)."""
    assert _shares_to_lots(0) == 0


def test_shares_to_lots_sub_hundred_floors_to_zero() -> None:
    """Fewer than 100 shares → 0 手 (integer floor, no rounding up)."""
    assert _shares_to_lots(99) == 0
    assert _shares_to_lots(150) == 1  # just over one lot → 1


def test_shares_to_lots_rejects_garbage() -> None:
    """Non-numeric input → None, never an exception."""
    assert _shares_to_lots("not-a-number") is None
    assert _shares_to_lots(math.nan) is None
