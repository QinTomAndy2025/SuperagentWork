"""A-share transaction-cost model used by the paper broker and risk checks."""

from __future__ import annotations

from dataclasses import dataclass

from .config import FeeConfig
from .models import Side


@dataclass(frozen=True)
class FeeBreakdown:
    commission: float
    stamp_duty: float
    transfer_fee: float

    @property
    def total(self) -> float:
        return round(self.commission + self.stamp_duty + self.transfer_fee, 2)


class FeeModel:
    """Commission with a minimum, sell-side stamp duty and transfer fee."""

    def __init__(self, config: FeeConfig):
        self._config = config

    def compute(self, side: Side, price: float, quantity: int) -> FeeBreakdown:
        amount = float(price) * int(quantity)
        if amount <= 0:
            return FeeBreakdown(0.0, 0.0, 0.0)
        commission = max(amount * self._config.commission_rate, self._config.min_commission)
        stamp_duty = amount * self._config.stamp_duty_rate if side is Side.SELL else 0.0
        transfer_fee = amount * self._config.transfer_fee_rate
        return FeeBreakdown(
            commission=round(commission, 2),
            stamp_duty=round(stamp_duty, 2),
            transfer_fee=round(transfer_fee, 2),
        )

    def estimate(self, side: Side, price: float, quantity: int) -> float:
        return self.compute(side, price, quantity).total

    def cash_required(self, side: Side, price: float, quantity: int) -> float:
        """Cash needed for a buy; for a sell this returns zero."""
        if side is not Side.BUY:
            return 0.0
        amount = float(price) * int(quantity)
        return round(amount + self.estimate(side, price, quantity), 2)

    def net_proceeds(self, side: Side, price: float, quantity: int) -> float:
        """Cash received for a sell; for a buy this returns zero."""
        if side is not Side.SELL:
            return 0.0
        amount = float(price) * int(quantity)
        return round(amount - self.estimate(side, price, quantity), 2)
