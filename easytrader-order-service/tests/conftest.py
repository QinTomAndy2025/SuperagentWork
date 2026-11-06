from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from easytrader_service.broker.paper import PaperBroker
from easytrader_service.config import (
    AppConfig,
    BrokerConfig,
    DatabaseConfig,
    EasytraderConfig,
    FeeConfig,
    PaperConfig,
    ServerConfig,
    TradingConfig,
)
from easytrader_service.models import (
    Order,
    OrderStatus,
    OrderType,
    Side,
    new_client_order_id,
    new_order_id,
)
from easytrader_service.store import OrderStore
from easytrader_service.timeutil import CHINA_TZ


class MutableClock:
    def __init__(self, when: datetime | None = None):
        self.when = when or datetime(2026, 11, 6, 10, 0, 0, tzinfo=CHINA_TZ)

    def __call__(self) -> datetime:
        return self.when

    def advance(self, **kwargs) -> datetime:
        self.when = self.when + timedelta(**kwargs)
        return self.when


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock()


@pytest.fixture
def config(tmp_path) -> AppConfig:
    return AppConfig(
        server=ServerConfig(api_key="test-api-key-1234567890"),
        database=DatabaseConfig(path=str(tmp_path / "orders.db")),
        trading=TradingConfig(
            dry_run=True,
            enforce_trading_session=False,
            max_order_amount=1_000_000.0,
            max_daily_amount=10_000_000.0,
            max_daily_orders=100,
            quantity_step=100,
            t_plus_one=True,
        ),
        fees=FeeConfig(),
        broker=BrokerConfig(
            type="paper",
            paper=PaperConfig(
                initial_cash=100_000.0,
                state_path=str(tmp_path / "paper_account.json"),
            ),
        ),
    )


@pytest.fixture
def store(config) -> OrderStore:
    store = OrderStore(config.database.path)
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def paper(config, clock) -> PaperBroker:
    return PaperBroker(
        config.broker.paper,
        config.fees,
        t_plus_one=config.trading.t_plus_one,
        now_fn=clock,
    )


@pytest.fixture
def order_factory():
    def make_order(**kwargs) -> Order:
        now = "2026-11-06T10:00:00+08:00"
        values = {
            "id": new_order_id(),
            "client_order_id": new_client_order_id("000001", Side.BUY),
            "symbol": "000001",
            "side": Side.BUY,
            "order_type": OrderType.LIMIT,
            "price": 10.0,
            "quantity": 100,
            "status": OrderStatus.RECEIVED,
            "broker_order_id": None,
            "filled_quantity": 0,
            "filled_price": None,
            "message": None,
            "source": "test",
            "remark": "",
            "prev_close": None,
            "trading_date": "2026-11-06",
            "created_at": now,
            "updated_at": now,
            "submitted_at": None,
            "raw": {},
        }
        values.update(kwargs)
        return Order(**values)

    return make_order


@pytest.fixture
def easytrader_config() -> EasytraderConfig:
    return EasytraderConfig(broker="ths", exe_path=r"C:\broker\xiadan.exe")
