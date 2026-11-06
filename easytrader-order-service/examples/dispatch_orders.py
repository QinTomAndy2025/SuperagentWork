#!/usr/bin/env python3
"""Send a small batch of dispatch orders to a running service.

Usage:
    ETS_URL=http://127.0.0.1:8000 ETS_API_KEY=... python examples/dispatch_orders.py

The example intentionally sends the same client_order_id twice to show that the
service is idempotent.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("ETS_URL", "http://127.0.0.1:8000").rstrip("/")
API_KEY = os.environ.get("ETS_API_KEY", "")

ORDERS = [
    {
        "client_order_id": "example-buy-001",
        "symbol": "000001",
        "side": "buy",
        "order_type": "limit",
        "price": 10.50,
        "quantity": 100,
        "source": "dispatch-example",
        "remark": "example buy",
    },
    {
        "client_order_id": "example-buy-001",
        "symbol": "000001",
        "side": "buy",
        "order_type": "limit",
        "price": 10.50,
        "quantity": 100,
        "source": "dispatch-example",
        "remark": "duplicate should not place a second order",
    },
    {
        "client_order_id": "example-sell-001",
        "symbol": "000001",
        "side": "sell",
        "order_type": "limit",
        "price": 10.80,
        "quantity": 100,
        "source": "dispatch-example",
        "remark": "example sell",
    },
]


def post_order(payload: dict) -> None:
    request = urllib.request.Request(
        f"{BASE_URL}/api/v1/orders",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}", file=sys.stderr)
        raise
    except urllib.error.URLError as exc:
        print(f"cannot reach {BASE_URL}: {exc.reason}", file=sys.stderr)
        raise
    order = body["order"]
    print(
        f"{order['client_order_id']:>18}  {order['side']:<4} {order['symbol']} "
        f"x{order['quantity']}  status={order['status']}  duplicate={body['duplicate']}"
    )


def main() -> int:
    if not API_KEY:
        print("set ETS_API_KEY first (read it from config.toml)", file=sys.stderr)
        return 1
    for payload in ORDERS:
        post_order(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
