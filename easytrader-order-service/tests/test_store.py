from __future__ import annotations

from easytrader_service.models import Order, OrderStatus


def test_create_get_and_duplicate(store, order_factory):
    order = order_factory(client_order_id="dispatch-1")
    assert store.create_order(order) is True
    assert store.create_order(order) is False

    row = store.get(order.id)
    assert row is not None
    assert row["client_order_id"] == "dispatch-1"
    assert row["status"] == "received"

    by_client = store.get_by_client_order_id("dispatch-1")
    assert by_client is not None
    assert by_client["id"] == order.id


def test_transition_is_atomic(store, order_factory):
    order = order_factory()
    store.create_order(order)

    assert store.transition("missing", ["received"], "accepted") is False
    assert store.transition(order.id, ["received"], "accepted") is True
    assert store.transition(order.id, ["received"], "rejected") is False
    assert store.get(order.id)["status"] == "accepted"


def test_update_fields_whitelist(store, order_factory):
    order = order_factory()
    store.create_order(order)
    try:
        store.update_fields(order.id, symbol="600000")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("symbol must not be updatable")
    assert store.update_fields(order.id, message="ok") is True


def test_daily_stats_only_counts_submitted_like_orders(store, order_factory):
    for status, symbol in [
        (OrderStatus.ACCEPTED, "000001"),
        (OrderStatus.FILLED, "000002"),
        (OrderStatus.REJECTED, "000003"),
        (OrderStatus.CANCELED, "000004"),
        (OrderStatus.FAILED, "000005"),
        (OrderStatus.UNKNOWN, "000006"),
    ]:
        store.create_order(
            order_factory(
                client_order_id=f"c-{symbol}",
                symbol=symbol,
                status=status,
                price=10.0,
                quantity=100,
            )
        )

    stats = store.daily_stats("2026-11-06")
    assert stats["count"] == 3  # accepted + filled + unknown
    assert stats["amount"] == 3000.0


def test_meta_and_audit(store, order_factory):
    order = order_factory()
    store.create_order(order)
    assert store.meta_get("paused") is None
    store.meta_set("paused", "1")
    assert store.meta_get("paused") == "1"

    store.audit(order.id, "received", {"request": {"symbol": order.symbol}})
    events = store.events(order.id)
    assert len(events) == 1
    assert events[0]["event"] == "received"
    assert events[0]["detail"]["request"]["symbol"] == order.symbol


def test_list_orders_and_counts(store, order_factory):
    for index in range(3):
        store.create_order(
            order_factory(
                client_order_id=f"list-{index}",
                status=OrderStatus.RECEIVED if index < 2 else OrderStatus.FILLED,
            )
        )
    assert len(store.list_orders()) == 3
    assert len(store.list_orders(status="received")) == 2
    assert store.counts() == {"received": 2, "filled": 1}
