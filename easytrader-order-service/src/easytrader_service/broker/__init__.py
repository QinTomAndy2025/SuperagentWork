"""Broker factory."""

from __future__ import annotations

from ..config import AppConfig
from ..errors import ConfigError
from .base import AccountSnapshot, Broker, CancelResult, Position, SubmitResult
from .easytrader_broker import EasytraderBroker
from .paper import PaperBroker

__all__ = [
    "AccountSnapshot",
    "Broker",
    "CancelResult",
    "EasytraderBroker",
    "PaperBroker",
    "Position",
    "SubmitResult",
    "build_broker",
]


def build_broker(config: AppConfig, *, persist_paper: bool = True) -> Broker:
    if config.broker.type == "paper":
        return PaperBroker(
            config.broker.paper,
            config.fees,
            t_plus_one=config.trading.t_plus_one,
            persist=persist_paper,
        )
    if config.broker.type == "easytrader":
        broker = EasytraderBroker(config.broker.easytrader)
        broker.connect()
        return broker
    raise ConfigError(f"未知 broker.type：{config.broker.type}")
