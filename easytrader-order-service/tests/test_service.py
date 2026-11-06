from __future__ import annotations

import pytest

from easytrader_service.broker.base import AccountSnapshot, CancelResult, SubmitResult
from easytrader_service.errors import PermanentBrokerError, TransientBrokerError, UnknownSubmitOutcome
from easytrader_service.models import OrderRequest, OrderStatus, OrderType, Side
from easytrader_service.service import OrderService


def request(**kwargs) -> OrderRequest:
    values = {
        "symbol": "000001",
        "side": Side.BUY,
        "price": 10.0,
        "quantity": 100,
    }
    values.update(kwargs)
    return OrderRequest(**values)


@pytest.fixture
def service(config, store, paper, clock) -> OrderService:
    service = OrderService(config, store, paper, now_fn=clock)
    service.initialize()
    return service


def test_submit_and_execute_buy(service):
    order, created = service.submit(request(client_order_id="dispatch-1"))
    assert created is True
    assert order.status is OrderStatus.RECEIVED

    service.process_pending()

    stored = service.get_order(order.id)
    assert stored is not None
    assert stored.status is OrderStatus.FILLED
    assert stored.broker_order_id.startswith("PAPER-")
    assert stored.filled_quantity == 100
    assert stored.submitted_at is not None


def test_duplicate_client_order_id_is_idempotent(service, paper):
    first, created = service.submit(request(client_order_id="same-id"))
    assert created is True
    service.process_pending()
    cash_after_first = paper.get_account().cash_available

    second, created_again = service.submit(request(client_order_id="same-id"))
    assert created_again is False
    assert second.id == first.id
    service.process_pending()

    assert len(service.list_orders()) == 1
    assert paper.get_account().cash_available == cash_after_first


def test_risk_rejection_is_persisted(service, paper):
    order, _ = service.submit(request(client_order_id="bad-lot", quantity=150))
    service.process_pending()

    stored = service.get_order(order.id)
    assert stored.status is OrderStatus.REJECTED
    assert "100" in (stored.message or "")
    assert paper.get_account().cash_available == 100_000.0
    events = service.events(order.id)
    assert any(event["event"] == "risk_rejected" for event in events)


def test_pause_keeps_order_queued_until_resume(service):
    service.pause("test")
    order, _ = service.submit(request(client_order_id="paused-1"))
    service.process_pending()
    assert service.get_order(order.id).status is OrderStatus.RECEIVED

    service.resume()
    service.process_pending()
    assert service.get_order(order.id).status is OrderStatus.FILLED


def test_cancel_queued_order(service):
    service.pause("test")
    order, _ = service.submit(request(client_order_id="cancel-1"))
    canceled = service.cancel(order.id, "user request")
    assert canceled.status is OrderStatus.CANCELED

    service.resume()
    service.process_pending()
    assert service.get_order(order.id).status is OrderStatus.CANCELED


def test_cancel_filled_order_conflicts(service):
    order, _ = service.submit(request(client_order_id="filled-1"))
    service.process_pending()
    with pytest.raises(Exception):
        service.cancel(order.id)


def test_recovery_marks_submitting_as_unknown(config, store, paper, clock, order_factory):
    order = order_factory(client_order_id="interrupted", status=OrderStatus.SUBMITTING)
    store.create_order(order)

    service = OrderService(config, store, paper, now_fn=clock)
    service.initialize()

    stored = service.get_order(order.id)
    assert stored.status is OrderStatus.UNKNOWN
    assert "人工核对" in (stored.message or "")


def test_initialize_requeues_received_orders(config, store, paper, clock, order_factory):
    order = order_factory(client_order_id="pending", status=OrderStatus.RECEIVED)
    store.create_order(order)

    service = OrderService(config, store, paper, now_fn=clock)
    service.initialize()
    service.process_pending()

    assert service.get_order(order.id).status is OrderStatus.FILLED


def test_sell_after_next_day(service, clock):
    buy, _ = service.submit(request(client_order_id="buy-1"))
    service.process_pending()
    assert service.get_order(buy.id).status is OrderStatus.FILLED

    clock.advance(days=1)
    sell, _ = service.submit(
        request(client_order_id="sell-1", side=Side.SELL, price=11.0)
    )
    service.process_pending()
    assert service.get_order(sell.id).status is OrderStatus.FILLED
    assert service.account().positions == []


class AccountFailingBroker:
    name = "failing"

    def connect(self):
        pass

    def get_account(self):
        raise TransientBrokerError("账户查询超时")

    def submit(self, order):
        raise AssertionError("submit must not be called")

    def cancel(self, broker_order_id):
        raise AssertionError("cancel must not be called")

    def close(self):
        pass


class UnknownSubmitBroker(AccountFailingBroker):
    name = "unknown"

    def get_account(self):
        return AccountSnapshot(cash_available=1_000_000.0, total_assets=1_000_000.0)

    def submit(self, order):
        raise UnknownSubmitOutcome("GUI timeout after click")


class RejectingBroker(UnknownSubmitBroker):
    name = "rejecting"

    def submit(self, order):
        raise PermanentBrokerError("资金不足")


@pytest.mark.parametrize(
    ("broker_cls", "expected"),
    [
        (AccountFailingBroker, OrderStatus.FAILED),
        (UnknownSubmitBroker, OrderStatus.UNKNOWN),
        (RejectingBroker, OrderStatus.FAILED),
    ],
)
def test_broker_failures_are_classified(config, store, clock, broker_cls, expected):
    service = OrderService(config, store, broker_cls(), now_fn=clock)
    service.initialize()
    order, _ = service.submit(request(client_order_id=f"failure-{broker_cls.__name__}"))
    service.process_pending()
    assert service.get_order(order.id).status is expected


def test_market_order_requires_reference_price():
    with pytest.raises(Exception):
        OrderRequest(symbol="000001", side=Side.BUY, order_type=OrderType.MARKET, quantity=100)


def test_stale_order_is_rejected(config, store, paper, clock, order_factory):
    order = order_factory(
        client_order_id="stale", status=OrderStatus.RECEIVED, trading_date="2026-11-05"
    )
    store.create_order(order)

    service = OrderService(config, store, paper, now_fn=clock)
    service.initialize()
    service.process_pending()

    stored = service.get_order(order.id)
    assert stored.status is OrderStatus.REJECTED
    assert "过期" in (stored.message or "")


def test_worker_can_start_and_stop(config, store, paper, clock):
    service = OrderService(config, store, paper, now_fn=clock)
    service.initialize()
    service.start_worker()
    assert service.stop_worker() is True


def test_status_and_counts(service):
    service.submit(request(client_order_id="count-1"))
    service.process_pending()
    status = service.status()
    assert status["paused"] is False
    assert status["broker"] == "paper"
    assert status["counts"].get("filled") == 1
