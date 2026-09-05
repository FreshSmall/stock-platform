"""A-share transaction cost model (V2.2 BP-V2.2-004 / T2.2).

Real A-share fee structure shared by the single-strategy (backtrader) path
and the vectorized portfolio backtest:

- commission: BOTH sides, 万2.5 typical, minimum 5 CNY per order;
- stamp duty: SELL side only, 万5 (rate halved 2023-08);
- transfer fee: both sides, 十万分之一;
- slippage: execution-price impact both sides — in the backtrader path the
  broker's percentage slippage plays this role; in the vectorized path it is
  applied to the fill price explicitly.

Pure functions + a backtrader CommInfo adapter; no DB access.
"""

from __future__ import annotations

from dataclasses import dataclass

import backtrader as bt


@dataclass(frozen=True)
class CostParams:
    """Default A-share fee schedule (rates as fractions, amounts in CNY)."""

    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_duty_rate: float = 0.0005  # sell only
    transfer_fee_rate: float = 0.00001
    slippage_rate: float = 0.001

    def buy_cost(self, amount: float) -> float:
        """Fees charged on a BUY of ``amount`` CNY."""
        return (
            max(amount * self.commission_rate, self.min_commission)
            + amount * self.transfer_fee_rate
        )

    def sell_cost(self, amount: float) -> float:
        """Fees charged on a SELL of ``amount`` CNY (adds stamp duty)."""
        return (
            max(amount * self.commission_rate, self.min_commission)
            + amount * self.transfer_fee_rate
            + amount * self.stamp_duty_rate
        )


class AShareCommission(bt.CommissionInfo):
    """backtrader commission with the asymmetric A-share fee structure.

    ``_getcommission`` receives ``size`` whose sign gives the side (buy > 0,
    sell < 0), so stamp duty lands on sells only and the per-order minimum
    commission kicks in on small tickets — two things the symmetric
    ``broker.setcommission`` cannot express.
    """

    params = (
        ("commission", 0.00025),
        ("min_commission", 5.0),
        ("stamp", 0.0005),
        ("transfer", 0.00001),
    )

    def _getcommission(self, size, price, pseudoexec):
        amount = abs(size) * price
        total = (
            max(amount * self.p.commission, self.p.min_commission)
            + amount * self.p.transfer
        )
        if size < 0:  # sell side
            total += amount * self.p.stamp
        return total
