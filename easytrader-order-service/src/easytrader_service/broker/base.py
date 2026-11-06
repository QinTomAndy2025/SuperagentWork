"""Broker abstraction shared by the paper simulator and easytrader adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..models import Order, OrderStatus


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: int
    available_quantity: int
    cost_price: float | None = None
    market_price: float | None = None


@dataclass(frozen=True)
class AccountSnapshot:
    cash_available: float | None
    total_assets: float | None
    positions: list[Position] = field(default_factory=list)
    fetched_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SubmitResult:
    broker_order_id: str | None
    status: OrderStatus
    filled_quantity: int = 0
    filled_price: float | None = None
    message: str = ""


@dataclass(frozen=True)
class CancelResult:
    success: bool
    message: str = ""


class Broker(Protocol):
    name: str

    def connect(self) -> None: ...

    def get_account(self) -> AccountSnapshot: ...

    def submit(self, order: Order) -> SubmitResult: ...

    def cancel(self, broker_order_id: str) -> CancelResult: ...

    def close(self) -> None: ...
