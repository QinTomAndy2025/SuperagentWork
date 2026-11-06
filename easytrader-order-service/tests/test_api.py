from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from easytrader_service.api import create_app


@pytest.fixture
def app(config):
    return create_app(config, start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def headers(config):
    return {"X-API-Key": config.server.api_key}


def order_payload(**kwargs):
    values = {
        "client_order_id": "api-1",
        "symbol": "000001",
        "side": "buy",
        "price": 10.0,
        "quantity": 100,
    }
    values.update(kwargs)
    return values


def test_health_does_not_require_auth(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["broker"] == "paper"


def test_orders_require_api_key(client):
    assert client.post("/api/v1/orders", json=order_payload()).status_code == 401
    assert client.get("/api/v1/orders").status_code == 401
    assert (
        client.post(
            "/api/v1/orders",
            json=order_payload(),
            headers={"X-API-Key": "wrong-key"},
        ).status_code
        == 401
    )


def test_submit_execute_and_query(client, app, config):
    response = client.post(
        "/api/v1/orders", json=order_payload(), headers=headers(config)
    )
    assert response.status_code == 202
    body = response.json()
    assert body["duplicate"] is False
    order_id = body["order"]["id"]

    app.state.service.process_pending()

    detail = client.get(f"/api/v1/orders/{order_id}", headers=headers(config))
    assert detail.status_code == 200
    assert detail.json()["status"] == "filled"
    assert detail.json()["filled_quantity"] == 100

    listing = client.get("/api/v1/orders", headers=headers(config))
    assert listing.status_code == 200
    assert len(listing.json()) == 1


def test_idempotent_submit(client, config):
    first = client.post(
        "/api/v1/orders", json=order_payload(), headers=headers(config)
    ).json()
    second = client.post(
        "/api/v1/orders", json=order_payload(), headers=headers(config)
    ).json()

    assert second["duplicate"] is True
    assert second["order"]["id"] == first["order"]["id"]
    assert len(client.get("/api/v1/orders", headers=headers(config)).json()) == 1


def test_account(client, app, config):
    client.post("/api/v1/orders", json=order_payload(), headers=headers(config))
    app.state.service.process_pending()

    response = client.get("/api/v1/account", headers=headers(config))
    assert response.status_code == 200
    body = response.json()
    assert body["cash_available"] == 98994.99
    assert body["positions"][0]["symbol"] == "000001"
    assert body["positions"][0]["available_quantity"] == 0


def test_pause_resume_and_cancel(client, config):
    pause = client.post(
        "/api/v1/control/pause",
        json={"paused": True, "reason": "risk review"},
        headers=headers(config),
    )
    assert pause.status_code == 200
    assert pause.json()["paused"] is True

    created = client.post(
        "/api/v1/orders",
        json=order_payload(client_order_id="paused-api"),
        headers=headers(config),
    ).json()
    order_id = created["order"]["id"]

    canceled = client.post(
        f"/api/v1/orders/{order_id}/cancel",
        json={"reason": "operator cancel"},
        headers=headers(config),
    )
    assert canceled.status_code == 200
    assert canceled.json()["status"] == "canceled"

    resume = client.post(
        "/api/v1/control/pause", json={"paused": False}, headers=headers(config)
    )
    assert resume.json()["paused"] is False


def test_events(client, app, config):
    created = client.post(
        "/api/v1/orders",
        json=order_payload(client_order_id="events-api"),
        headers=headers(config),
    ).json()
    order_id = created["order"]["id"]
    app.state.service.process_pending()

    response = client.get(
        f"/api/v1/orders/{order_id}/events", headers=headers(config)
    )
    assert response.status_code == 200
    events = [item["event"] for item in response.json()]
    assert "received" in events
    assert "risk_approved" in events
    assert "broker_result" in events


def test_invalid_payload_and_unknown_order(client, config):
    response = client.post(
        "/api/v1/orders",
        json=order_payload(symbol="ABC"),
        headers=headers(config),
    )
    assert response.status_code == 422

    missing = client.get("/api/v1/orders/does-not-exist", headers=headers(config))
    assert missing.status_code == 404


def test_status_endpoint(client, config):
    response = client.get("/api/v1/control/status", headers=headers(config))
    assert response.status_code == 200
    body = response.json()
    assert body["paused"] is False
    assert body["dry_run"] is True
