"""Data models shared by quote sources, tools and the CLI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


@dataclass
class BookLevel:
    price: float
    volume_lots: float

    def to_dict(self) -> dict[str, float]:
        return {
            "price": _round(self.price),
            "volume_lots": _round(self.volume_lots, 2),
        }


@dataclass
class Quote:
    symbol: str
    code: str
    market: str
    name: str
    price: float | None = None
    prev_close: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    change: float | None = None
    change_pct: float | None = None
    volume_lots: float | None = None
    amount_yuan: float | None = None
    turnover_pct: float | None = None
    limit_up: float | None = None
    limit_down: float | None = None
    bids: list[BookLevel] = field(default_factory=list)
    asks: list[BookLevel] = field(default_factory=list)
    timestamp: str | None = None
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "code": self.code,
            "market": self.market,
            "name": self.name,
            "price": _round(self.price),
            "prev_close": _round(self.prev_close),
            "open": _round(self.open),
            "high": _round(self.high),
            "low": _round(self.low),
            "change": _round(self.change),
            "change_pct": _round(self.change_pct),
            "volume_lots": _round(self.volume_lots, 2),
            "amount_yuan": _round(self.amount_yuan, 2),
            "turnover_pct": _round(self.turnover_pct),
            "limit_up": _round(self.limit_up),
            "limit_down": _round(self.limit_down),
            "bids": [level.to_dict() for level in self.bids],
            "asks": [level.to_dict() for level in self.asks],
            "timestamp": self.timestamp,
            "source": self.source,
        }


@dataclass
class QuoteBatch:
    quotes: list[Quote] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": len(self.quotes),
            "quotes": [quote.to_dict() for quote in self.quotes],
            "errors": list(self.errors),
        }


@dataclass
class MinuteBar:
    time: str
    price: float
    volume_lots: float
    amount_yuan: float
    avg_price: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "time": self.time,
            "price": _round(self.price),
            "volume_lots": _round(self.volume_lots, 2),
            "amount_yuan": _round(self.amount_yuan, 2),
            "avg_price": _round(self.avg_price),
        }


@dataclass
class IntradayResult:
    symbol: str
    name: str
    date: str | None
    bars: list[MinuteBar]
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "date": self.date,
            "count": len(self.bars),
            "source": self.source,
            "bars": [bar.to_dict() for bar in self.bars],
        }
