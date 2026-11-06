from __future__ import annotations

import pytest

from easytrader_service.broker.paper import PaperBroker
from easytrader_service.errors import InsufficientPosition
from easytrader_service.models import OrderStatus, OrderType, Side


def test_initial_paper_account(config, clock):
    broker = PaperBroker(config.broker.paper, config.fees, now_fn=clock)
    snapshot = broker.get_account()
    assert snapshot.cash_available == 100_000.0
    assert snapshot.total_assets == 100_000.0
    assert snapshot.positions == []


def test_buy_fills_and_charges_fees(config, clock, order_factory):
    broker = PaperBroker(config.broker.paper, config.fees, now_fn=clock)
    result = broker.submit(
        order_factory(side=Side.BUY, price=10.0, quantity=100)
    )

    assert result.status is OrderStatus.FILLED
    assert result.filled_quantity == 100
    assert result.broker_order_id.startswith("PAPER-")
    snapshot = broker.get_account()
    assert snapshot.cash_available == 98_994.99
    assert snapshot.positions[0].quantity == 100
    assert snapshot.positions[0].available_quantity == 0  # T+1
    assert snapshot.positions[0].cost_price == 10.0501


def test_t_plus_one_blocks_same_day_sell(config, clock, order_factory):
    broker = PaperBroker(config.broker.paper, config.fees, now_fn=clock)
    broker.submit(order_factory(side=Side.BUY, price=10.0, quantity=100))

    with pytest.raises(InsufficientPosition):
        broker.submit(order_factory(side=Side.SELL, price=10.5, quantity=100))


def test_next_day_sell_fills(config, clock, order_factory):
    broker = PaperBroker(config.broker.paper, config.fees, now_fn=clock)
    broker.submit(order_factory(side=Side.BUY, price=10.0, quantity=100))

    clock.advance(days=1)
    snapshot = broker.get_account()
    assert snapshot.positions[0].available_quantity == 100

    result = broker.submit(order_factory(side=Side.SELL, price=11.0, quantity=100))
    assert result.status is OrderStatus.FILLED
    snapshot = broker.get_account()
    assert snapshot.positions == []
    assert snapshot.cash_available == 100_089.43


def test_t_plus_one_disabled_allows_same_day_sell(config, clock, order_factory):
    broker = PaperBroker(
        config.broker.paper, config.fees, t_plus_one=False, now_fn=clock
    )
    broker.submit(order_factory(side=Side.BUY, price=10.0, quantity=100))
    result = broker.submit(order_factory(side=Side.SELL, price=10.0, quantity=100))
    assert result.status is OrderStatus.FILLED


def test_insufficient_cash_is_rejected(config, clock, order_factory):
    paper_config = config.broker.paper.model_copy(update={"initial_cash": 500.0})
    broker = PaperBroker(paper_config, config.fees, now_fn=clock)
    from easytrader_service.errors import InsufficientFunds

    with pytest.raises(InsufficientFunds):
        broker.submit(order_factory(side=Side.BUY, price=10.0, quantity=100))


def test_market_order_uses_reference_price(config, clock, order_factory):
    broker = PaperBroker(config.broker.paper, config.fees, now_fn=clock)
    result = broker.submit(
        order_factory(order_type=OrderType.MARKET, price=10.0, quantity=100)
    )
    assert result.status is OrderStatus.FILLED
    assert result.filled_price == 10.0


def test_state_persists_across_instances(config, clock, order_factory):
    first = PaperBroker(config.broker.paper, config.fees, now_fn=clock)
    first.submit(order_factory(side=Side.BUY, price=10.0, quantity=100))

    second = PaperBroker(config.broker.paper, config.fees, now_fn=clock)
    snapshot = second.get_account()
    assert snapshot.cash_available == 98_994.99
    assert snapshot.positions[0].quantity == 100


def test_reset_restores_initial_cash(config, clock, order_factory):
    broker = PaperBroker(config.broker.paper, config.fees, now_fn=clock)
    broker.submit(order_factory(side=Side.BUY, price=10.0, quantity=100))
    broker.reset()
    snapshot = broker.get_account()
    assert snapshot.cash_available == 100_000.0
    assert snapshot.positions == []
