"""SQLite persistence for orders, audit events and service metadata."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from .models import COUNTED_STATUSES, Order
from .timeutil import iso_now

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    client_order_id TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    price REAL NOT NULL,
    quantity INTEGER NOT NULL,
    status TEXT NOT NULL,
    broker_order_id TEXT,
    filled_quantity INTEGER NOT NULL DEFAULT 0,
    filled_price REAL,
    message TEXT,
    source TEXT NOT NULL DEFAULT '',
    remark TEXT NOT NULL DEFAULT '',
    prev_close REAL,
    trading_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    submitted_at TEXT,
    raw TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_trading_date ON orders(trading_date);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}',
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_order ON audit(order_id);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_UPDATABLE_FIELDS = {
    "status",
    "broker_order_id",
    "filled_quantity",
    "filled_price",
    "message",
    "submitted_at",
    "updated_at",
}


class OrderStore:
    """Thread-safe SQLite store shared by the API and the worker thread."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path), check_same_thread=False, timeout=30
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def initialize(self) -> None:
        """Schema is created in the constructor; kept for a symmetric lifecycle."""

    # ------------------------------------------------------------------ orders
    def create_order(self, order: Order) -> bool:
        """Insert a new order.  Return False when client_order_id already exists."""
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO orders (
                        id, client_order_id, symbol, side, order_type, price,
                        quantity, status, broker_order_id, filled_quantity,
                        filled_price, message, source, remark, prev_close,
                        trading_date, created_at, updated_at, submitted_at, raw
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order.id,
                        order.client_order_id,
                        order.symbol,
                        order.side.value,
                        order.order_type.value,
                        order.price,
                        order.quantity,
                        order.status.value,
                        order.broker_order_id,
                        order.filled_quantity,
                        order.filled_price,
                        order.message,
                        order.source,
                        order.remark,
                        order.prev_close,
                        order.trading_date,
                        order.created_at,
                        order.updated_at,
                        order.submitted_at,
                        json.dumps(order.raw, ensure_ascii=False),
                    ),
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                self._conn.rollback()
                return False

    def get(self, order_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return dict(row) if row else None

    def get_by_client_order_id(self, client_order_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_orders(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM orders WHERE status = ? "
                    "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM orders ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    def find_by_statuses(self, statuses: Iterable[str]) -> list[dict[str, Any]]:
        values = [str(value) for value in statuses]
        if not values:
            return []
        placeholders = ",".join("?" for _ in values)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM orders WHERE status IN ({placeholders}) "
                "ORDER BY created_at ASC, rowid ASC",
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def transition(
        self,
        order_id: str,
        from_statuses: Iterable[str],
        to_status: str,
        **fields: Any,
    ) -> bool:
        """Atomically move an order between statuses.

        Returns False when the order is no longer in one of ``from_statuses``,
        which prevents a late worker result from resurrecting a canceled order.
        """
        status_values = [str(value) for value in from_statuses]
        allowed = ",".join("?" for _ in status_values)
        assignments = ["status = ?", "updated_at = ?"]
        params: list[Any] = [to_status, iso_now()]
        for key, value in fields.items():
            if key not in _UPDATABLE_FIELDS or key in {"status", "updated_at"}:
                raise ValueError(f"不允许更新字段：{key}")
            assignments.append(f"{key} = ?")
            params.append(value)
        params.extend([order_id, *status_values])
        sql = (
            f"UPDATE orders SET {', '.join(assignments)} "
            f"WHERE id = ? AND status IN ({allowed})"
        )
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor.rowcount == 1

    def update_fields(self, order_id: str, **fields: Any) -> bool:
        assignments = ["updated_at = ?"]
        params: list[Any] = [iso_now()]
        for key, value in fields.items():
            if key not in _UPDATABLE_FIELDS or key == "updated_at":
                raise ValueError(f"不允许更新字段：{key}")
            assignments.append(f"{key} = ?")
            params.append(value)
        params.append(order_id)
        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE orders SET {', '.join(assignments)} WHERE id = ?", params
            )
            self._conn.commit()
            return cursor.rowcount == 1

    def daily_stats(self, trading_date: str) -> dict[str, float | int]:
        counted = [status.value for status in COUNTED_STATUSES]
        placeholders = ",".join("?" for _ in counted)
        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT COUNT(*) AS count, COALESCE(SUM(price * quantity), 0) AS amount
                FROM orders
                WHERE trading_date = ? AND status IN ({placeholders})
                """,
                (trading_date, *counted),
            ).fetchone()
        return {"count": int(row["count"]), "amount": float(row["amount"])}

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS count FROM orders GROUP BY status"
            ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    # ------------------------------------------------------------------- meta
    def meta_get(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def meta_set(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()

    # ------------------------------------------------------------------ audit
    def audit(self, order_id: str, event: str, detail: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit(order_id, event, detail, at) VALUES (?, ?, ?, ?)",
                (order_id, event, json.dumps(detail or {}, ensure_ascii=False), iso_now()),
            )
            self._conn.commit()

    def events(self, order_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit WHERE order_id = ? ORDER BY id ASC", (order_id,)
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["detail"] = json.loads(item.get("detail") or "{}")
            except json.JSONDecodeError:
                item["detail"] = {}
            result.append(item)
        return result
