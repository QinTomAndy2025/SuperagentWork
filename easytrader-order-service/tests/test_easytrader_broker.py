from __future__ import annotations

import pytest

from easytrader_service.broker.easytrader_broker import EasytraderBroker
from easytrader_service.errors import (
    BrokerUnavailable,
    PermanentBrokerError,
    UnknownSubmitOutcome,
)
from easytrader_service.models import OrderStatus, OrderType, Side


class FakeUser:
    def __init__(self, balance=None, positions=None):
        self._balance = balance if balance is not None else {"可用金额": 50000.0, "总资产": 60000.0}
        self._positions = (
            positions
            if positions is not None
            else [
                {
                    "证券代码": "000001",
                    "股票余额": 500,
                    "可用余额": 400,
                    "成本价": 9.5,
                    "市价": 10.0,
                }
            ]
        )
        self.connected = False
        self.connect_args = None
        self.calls = []

    def connect(self, *args, **kwargs):
        self.connected = True
        self.connect_args = (args, kwargs)

    @property
    def balance(self):
        return self._balance

    @property
    def position(self):
        return self._positions

    def buy(self, security, price, amount):
        self.calls.append(("buy", security, price, amount))
        return {"entrust_no": "E100"}

    def sell(self, security, price, amount):
        self.calls.append(("sell", security, price, amount))
        return {"entrust_no": "E101"}

    def market_buy(self, security, amount, ttype=None):
        self.calls.append(("market_buy", security, amount, ttype))
        return [{"entrust_no": "E102", "message": "委托成功"}]

    def market_sell(self, security, amount, ttype=None):
        self.calls.append(("market_sell", security, amount, ttype))
        return {"entrust_no": "E103"}

    def cancel_entrust(self, entrust_no):
        self.calls.append(("cancel", entrust_no))
        return {"message": "撤单成功"}


class FakeModule:
    def __init__(self, user):
        self.user = user
        self.used = None

    def use(self, broker, **kwargs):
        self.used = broker
        return self.user


def test_connect_and_account_normalisation(easytrader_config):
    user = FakeUser()
    broker = EasytraderBroker(
        easytrader_config, easytrader_module=FakeModule(user)
    )
    broker.connect()

    assert user.connected is True
    snapshot = broker.get_account()
    assert snapshot.cash_available == 50000.0
    assert snapshot.total_assets == 60000.0
    assert len(snapshot.positions) == 1
    assert snapshot.positions[0].symbol == "000001"
    assert snapshot.positions[0].quantity == 500
    assert snapshot.positions[0].available_quantity == 400


def test_limit_buy_and_sell(easytrader_config, order_factory):
    user = FakeUser()
    broker = EasytraderBroker(easytrader_config, easytrader_module=FakeModule(user))
    broker.connect()

    buy = broker.submit(order_factory(side=Side.BUY, price=10.0, quantity=100))
    assert buy.status is OrderStatus.SUBMITTED
    assert buy.broker_order_id == "E100"
    assert user.calls[-1] == ("buy", "000001", 10.0, 100)

    sell = broker.submit(order_factory(side=Side.SELL, price=10.5, quantity=100))
    assert sell.status is OrderStatus.SUBMITTED
    assert sell.broker_order_id == "E101"
    assert user.calls[-1] == ("sell", "000001", 10.5, 100)


def test_market_order_passes_ttype(easytrader_config, order_factory):
    user = FakeUser()
    config = easytrader_config.model_copy(update={"market_ttype": "对手方最优价格委托"})
    broker = EasytraderBroker(config, easytrader_module=FakeModule(user))
    broker.connect()

    result = broker.submit(
        order_factory(order_type=OrderType.MARKET, price=10.0, quantity=100)
    )
    assert result.status is OrderStatus.SUBMITTED
    assert result.broker_order_id == "E102"
    assert user.calls[-1] == ("market_buy", "000001", 100, "对手方最优价格委托")


def test_cancel(easytrader_config):
    user = FakeUser()
    broker = EasytraderBroker(easytrader_config, easytrader_module=FakeModule(user))
    broker.connect()

    result = broker.cancel("12345")
    assert result.success is True
    assert user.calls[-1] == ("cancel", "12345")


def test_cancel_failure_message_is_permanent(easytrader_config):
    user = FakeUser()
    user.cancel_entrust = lambda entrust_no: {"message": "委托单状态错误不能撤单"}
    broker = EasytraderBroker(easytrader_config, easytrader_module=FakeModule(user))
    broker.connect()

    with pytest.raises(PermanentBrokerError):
        broker.cancel("12345")


def test_trade_error_is_permanent(easytrader_config, order_factory):
    class TradeError(IOError):
        pass

    user = FakeUser()

    def raise_trade_error(*args, **kwargs):
        raise TradeError("资金不足")

    user.buy = raise_trade_error
    broker = EasytraderBroker(easytrader_config, easytrader_module=FakeModule(user))
    broker.connect()

    with pytest.raises(PermanentBrokerError):
        broker.submit(order_factory())


def test_unexpected_exception_is_unknown(easytrader_config, order_factory):
    user = FakeUser()

    def raise_timeout(*args, **kwargs):
        raise TimeoutError("GUI timeout")

    user.buy = raise_timeout
    broker = EasytraderBroker(easytrader_config, easytrader_module=FakeModule(user))
    broker.connect()

    with pytest.raises(UnknownSubmitOutcome):
        broker.submit(order_factory())


def test_negative_entrust_number_is_permanent(easytrader_config, order_factory):
    user = FakeUser()
    user.buy = lambda *args, **kwargs: {"entrust_no": -1}
    broker = EasytraderBroker(easytrader_config, easytrader_module=FakeModule(user))
    broker.connect()

    with pytest.raises(PermanentBrokerError):
        broker.submit(order_factory())


def test_miniqmt_style_balance_list_is_supported(easytrader_config, order_factory):
    user = FakeUser(
        balance=[{"cash": 12345.0, "total_asset": 20000.0}],
        positions=[
            {
                "stock_code": "600000.SH",
                "volume": 200,
                "can_use_volume": 200,
                "open_price": 8.0,
            }
        ],
    )
    broker = EasytraderBroker(easytrader_config, easytrader_module=FakeModule(user))
    broker.connect()

    snapshot = broker.get_account()
    assert snapshot.cash_available == 12345.0
    assert snapshot.total_assets == 20000.0
    assert snapshot.positions[0].symbol == "600000"
    assert snapshot.positions[0].available_quantity == 200


def test_connect_failure_is_reported(easytrader_config):
    class BrokenModule:
        def use(self, broker, **kwargs):
            raise RuntimeError("client not running")

    broker = EasytraderBroker(easytrader_config, easytrader_module=BrokenModule())
    with pytest.raises(BrokerUnavailable):
        broker.connect()


def test_unconnected_broker_reports_unavailable(easytrader_config):
    broker = EasytraderBroker(easytrader_config, easytrader_module=FakeModule(FakeUser()))
    with pytest.raises(BrokerUnavailable):
        broker.get_account()
