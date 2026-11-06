"""Order domain models and the HTTP request schema."""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    LIMIT = "limit"
    MARKET = "market"


class OrderStatus(str, Enum):
    RECEIVED = "received"
    ACCEPTED = "accepted"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_PENDING = "cancel_pending"
    CANCELED = "canceled"
    REJECTED = "rejected"
    FAILED = "failed"
    UNKNOWN = "unknown"


TERMINAL_STATUSES = {
    OrderStatus.FILLED,
    OrderStatus.CANCELED,
    OrderStatus.REJECTED,
    OrderStatus.FAILED,
    OrderStatus.UNKNOWN,
}

# Statuses that count against the daily order/amount limits.  "unknown" counts
# because the order may have reached the broker.
COUNTED_STATUSES = {
    OrderStatus.ACCEPTED,
    OrderStatus.SUBMITTING,
    OrderStatus.SUBMITTED,
    OrderStatus.PARTIALLY_FILLED,
    OrderStatus.FILLED,
    OrderStatus.CANCEL_PENDING,
    OrderStatus.UNKNOWN,
}

_SYMBOL_RE = re.compile(r"\d{6}")
_BOARD_BAND = (
    (("300", "301"), 0.20),  # ChiNext
    (("688", "689"), 0.20),  # STAR market
    (("4", "8"), 0.30),  # Beijing Stock Exchange
)


def normalize_symbol(value: str) -> str:
    """Normalise common A-share symbol spellings to six digits.

    Accepted examples: ``000001``, ``SZ000001``, ``000001.SZ``, ``sh600000``.
    """
    raw = str(value).strip().upper().replace(" ", "")
    for prefix in ("SH", "SZ", "BJ"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
            break
    if "." in raw:
        raw = raw.split(".", 1)[0]
    if not _SYMBOL_RE.fullmatch(raw):
        raise ValueError("A 股代码必须是 6 位数字，例如 000001 / 600000")
    return raw


def price_band_for(symbol: str, base_pct: float) -> float:
    """Return the daily price-limit band for a symbol.

    This is a best-effort helper: ST stocks have a 5% band and the caller can
    disable the check when the previous close or board rules are unknown.
    """
    for prefixes, pct in _BOARD_BAND:
        if symbol.startswith(prefixes):
            return pct
    return base_pct


class OrderRequest(BaseModel):
    """Payload accepted from a dispatch source."""

    model_config = ConfigDict(extra="forbid")

    client_order_id: str | None = Field(default=None, max_length=64)
    symbol: str
    side: Side
    order_type: OrderType = OrderType.LIMIT
    price: float | None = None
    reference_price: float | None = None
    prev_close: float | None = None
    quantity: int
    source: str = Field(default="api", max_length=64)
    remark: str = Field(default="", max_length=500)

    @field_validator("symbol")
    @classmethod
    def _validate_symbol(cls, value: str) -> str:
        return normalize_symbol(value)

    @field_validator("client_order_id", "source", "remark")
    @classmethod
    def _strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(value).strip()

    @field_validator("price", "reference_price", "prev_close")
    @classmethod
    def _positive_optional_price(cls, value: float | None) -> float | None:
        if value is None:
            return None
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            raise ValueError("价格必须是大于 0 的有限数值")
        return number

    @field_validator("quantity")
    @classmethod
    def _positive_quantity(cls, value: int) -> int:
        number = int(value)
        if number <= 0:
            raise ValueError("委托数量必须大于 0")
        return number

    @model_validator(mode="after")
    def _require_price(self) -> "OrderRequest":
        if self.order_type is OrderType.LIMIT:
            if self.price is None:
                raise ValueError("限价委托必须提供 price")
        else:
            if self.price is None and self.reference_price is None:
                raise ValueError("市价委托必须提供 reference_price（用于风控与模拟成交）")
        if self.client_order_id is not None and not self.client_order_id:
            raise ValueError("client_order_id 不能为空字符串")
        return self

    @property
    def effective_price(self) -> float:
        """Price used for risk estimation, order amount and paper fills."""
        if self.order_type is OrderType.MARKET:
            selected = self.reference_price if self.reference_price is not None else self.price
        else:
            selected = self.price
        assert selected is not None  # guaranteed by the model validator
        return float(selected)


@dataclass(frozen=True)
class Order:
    """Persisted order state."""

    id: str
    client_order_id: str
    symbol: str
    side: Side
    order_type: OrderType
    price: float
    quantity: int
    status: OrderStatus
    broker_order_id: str | None
    filled_quantity: int
    filled_price: float | None
    message: str | None
    source: str
    remark: str
    prev_close: float | None
    trading_date: str
    created_at: str
    updated_at: str
    submitted_at: str | None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "Order":
        raw = row.get("raw") or "{}"
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {}
        return cls(
            id=str(row["id"]),
            client_order_id=str(row["client_order_id"]),
            symbol=str(row["symbol"]),
            side=Side(row["side"]),
            order_type=OrderType(row["order_type"]),
            price=float(row["price"]),
            quantity=int(row["quantity"]),
            status=OrderStatus(row["status"]),
            broker_order_id=row.get("broker_order_id"),
            filled_quantity=int(row.get("filled_quantity") or 0),
            filled_price=(float(row["filled_price"]) if row.get("filled_price") is not None else None),
            message=row.get("message"),
            source=str(row.get("source") or ""),
            remark=str(row.get("remark") or ""),
            prev_close=(float(row["prev_close"]) if row.get("prev_close") is not None else None),
            trading_date=str(row["trading_date"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            submitted_at=row.get("submitted_at"),
            raw=raw if isinstance(raw, dict) else {},
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "price": self.price,
            "quantity": self.quantity,
            "status": self.status.value,
            "broker_order_id": self.broker_order_id,
            "filled_quantity": self.filled_quantity,
            "filled_price": self.filled_price,
            "message": self.message,
            "source": self.source,
            "remark": self.remark,
            "prev_close": self.prev_close,
            "trading_date": self.trading_date,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "submitted_at": self.submitted_at,
        }


def new_order_id() -> str:
    return uuid.uuid4().hex


def new_client_order_id(symbol: str, side: Side) -> str:
    return f"{symbol}-{side.value}-{uuid.uuid4().hex[:16]}"
