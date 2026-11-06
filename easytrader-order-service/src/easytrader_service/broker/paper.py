"""In-process paper-trading broker used for dry runs and tests.

It fills limit and market orders immediately at the requested/reference price,
models commission/stamp duty, and optionally enforces A-share T+1 settlement.
It never talks to a broker client or the network.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..config import FeeConfig, PaperConfig
from ..errors import InsufficientFunds, InsufficientPosition, PermanentBrokerError
from ..fees import FeeModel
from ..models import Order, OrderStatus, Side
from ..timeutil import iso_now, now_china
from .base import AccountSnapshot, CancelResult, Position, SubmitResult


class PaperBroker:
    name = "paper"

    def __init__(
        self,
        config: PaperConfig,
        fees: FeeConfig,
        *,
        t_plus_one: bool = True,
        now_fn: Callable[[], datetime] | None = None,
        persist: bool = True,
    ):
        self._config = config
        self._fees = FeeModel(fees)
        self._t_plus_one = t_plus_one
        self._now_fn = now_fn or now_china
        self._persist = persist
        self._lock = threading.RLock()
        self._state_path = Path(config.state_path)
        self._state = self._load_state()
        self._settle_locked()
        self._save_locked()

    # ---------------------------------------------------------------- state
    def _default_state(self) -> dict[str, Any]:
        return {
            "version": 1,
            "initial_cash": float(self._config.initial_cash),
            "cash": float(self._config.initial_cash),
            "positions": {},
            "settlement_date": self._today(),
            "sequence": 0,
        }

    def _load_state(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return self._default_state()
        with self._state_path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
        default = self._default_state()
        for key, value in default.items():
            state.setdefault(key, value)
        state["positions"] = {
            str(symbol): {
                "quantity": int(item.get("quantity", 0)),
                "available": int(item.get("available", 0)),
                "cost_price": float(item.get("cost_price", 0.0)),
                "last_price": float(item.get("last_price", 0.0)),
            }
            for symbol, item in (state.get("positions") or {}).items()
            if int(item.get("quantity", 0)) > 0
        }
        return state

    def _save_locked(self) -> None:
        if not self._persist:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(self._state, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, self._state_path)

    def _today(self) -> str:
        return self._now_fn().date().isoformat()

    def _settle_locked(self) -> None:
        """Make yesterday's buys sellable once a new trading day starts."""
        today = self._today()
        if self._state.get("settlement_date") == today:
            return
        for item in self._state["positions"].values():
            item["available"] = int(item["quantity"])
        self._state["settlement_date"] = today

    # --------------------------------------------------------------- broker
    def connect(self) -> None:
        return None

    def close(self) -> None:
        return None

    def get_account(self) -> AccountSnapshot:
        with self._lock:
            self._settle_locked()
            positions = [
                Position(
                    symbol=symbol,
                    quantity=int(item["quantity"]),
                    available_quantity=int(item["available"]),
                    cost_price=round(float(item["cost_price"]), 4),
                    market_price=round(float(item["last_price"]), 4),
                )
                for symbol, item in sorted(self._state["positions"].items())
            ]
            market_value = sum(
                float(item["quantity"]) * float(item["last_price"])
                for item in self._state["positions"].values()
            )
            cash = round(float(self._state["cash"]), 2)
            return AccountSnapshot(
                cash_available=cash,
                total_assets=round(cash + market_value, 2),
                positions=positions,
                fetched_at=iso_now(),
                raw={"mode": "paper", "settlement_date": self._state["settlement_date"]},
            )

    def submit(self, order: Order) -> SubmitResult:
        with self._lock:
            self._settle_locked()
            amount = round(float(order.price) * int(order.quantity), 2)
            fee = self._fees.estimate(order.side, order.price, order.quantity)
            if order.side is Side.BUY:
                required = round(amount + fee, 2)
                cash = float(self._state["cash"])
                if cash + 1e-9 < required:
                    raise InsufficientFunds(
                        f"模拟账户可用资金不足：需要 {required:.2f}，可用 {cash:.2f}"
                    )
                self._state["cash"] = round(cash - required, 2)
                self._apply_buy_locked(order.symbol, order.quantity, order.price, amount, fee)
            else:
                position = self._state["positions"].get(order.symbol)
                if position is None or int(position["available"]) < order.quantity:
                    available = int(position["available"]) if position else 0
                    raise InsufficientPosition(
                        f"模拟账户可卖数量不足：需要 {order.quantity}，可用 {available}"
                    )
                proceeds = round(amount - fee, 2)
                self._state["cash"] = round(float(self._state["cash"]) + proceeds, 2)
                position["quantity"] = int(position["quantity"]) - order.quantity
                position["available"] = int(position["available"]) - order.quantity
                position["last_price"] = float(order.price)
                if int(position["quantity"]) <= 0:
                    del self._state["positions"][order.symbol]

            self._state["sequence"] = int(self._state["sequence"]) + 1
            sequence = int(self._state["sequence"])
            self._save_locked()
            return SubmitResult(
                broker_order_id=f"PAPER-{sequence:08d}",
                status=OrderStatus.FILLED,
                filled_quantity=order.quantity,
                filled_price=order.price,
                message="模拟盘即时成交",
            )

    def _apply_buy_locked(
        self, symbol: str, quantity: int, price: float, amount: float, fee: float
    ) -> None:
        position = self._state["positions"].setdefault(
            symbol,
            {"quantity": 0, "available": 0, "cost_price": 0.0, "last_price": 0.0},
        )
        old_quantity = int(position["quantity"])
        new_quantity = old_quantity + quantity
        total_cost = float(position["cost_price"]) * old_quantity + amount + fee
        position["quantity"] = new_quantity
        position["cost_price"] = round(total_cost / new_quantity, 4) if new_quantity else 0.0
        position["available"] = int(position["available"]) + (0 if self._t_plus_one else quantity)
        position["last_price"] = float(price)

    def cancel(self, broker_order_id: str) -> CancelResult:
        raise PermanentBrokerError("模拟盘订单即时成交，没有可撤销的在途委托")

    def reset(self) -> None:
        with self._lock:
            self._state = self._default_state()
            self._save_locked()

    def account_state(self) -> dict[str, Any]:
        """Expose a copy of the raw paper state for the CLI/doctor output."""
        with self._lock:
            return json.loads(json.dumps(self._state))
