"""Order intake, risk gating, execution worker and cancellation."""

from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime
from typing import Any, Callable

from .broker.base import Broker
from .config import AppConfig
from .errors import (
    BrokerError,
    BrokerUnavailable,
    ConflictError,
    NotFoundError,
    PermanentBrokerError,
    UnknownSubmitOutcome,
)
from .fees import FeeModel
from .models import (
    Order,
    OrderRequest,
    OrderStatus,
    new_client_order_id,
    new_order_id,
)
from .risk import DailyStats, RiskEngine
from .store import OrderStore
from .timeutil import now_china, trading_date_of
from .trading_calendar import TradingCalendar

logger = logging.getLogger(__name__)


class OrderService:
    """Coordinate intake, risk checks, broker execution and persistence.

    The HTTP API only enqueues accepted orders; a single worker thread performs
    the broker calls.  A single worker is intentional: easytrader drives a GUI
    client and is not safe to call concurrently.
    """

    def __init__(
        self,
        config: AppConfig,
        store: OrderStore,
        broker: Broker,
        *,
        calendar: TradingCalendar | None = None,
        fees: FeeModel | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ):
        self.config = config
        self.store = store
        self.broker = broker
        self._now_fn = now_fn or now_china
        self._risk = RiskEngine(config, calendar=calendar, fees=fees)
        self._queue: queue.Queue[str] = queue.Queue()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._state_lock = threading.RLock()
        self._paused = False
        self._pause_reason = ""
        self._initialized = False
        self._last_error: str | None = None

    # -------------------------------------------------------------- lifecycle
    def initialize(self) -> None:
        with self._state_lock:
            if self._initialized:
                return
            self.store.initialize()
            # An order interrupted during submission may already be at the
            # broker.  Never resubmit it automatically.
            for row in self.store.find_by_statuses(["submitting"]):
                message = "服务重启时订单正处于提交中，可能已报单，请人工核对券商委托列表"
                self.store.transition(row["id"], ["submitting"], "unknown", message=message)
                self.store.audit(row["id"], "recovered_unknown", {"message": message})
            self._paused = self.store.meta_get("paused") == "1"
            self._pause_reason = self.store.meta_get("pause_reason") or ""
            for row in self.store.find_by_statuses(["received", "accepted"]):
                self._queue.put(row["id"])
            self._initialized = True

    def start_worker(self) -> None:
        with self._state_lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._stop_event.clear()
            self._worker = threading.Thread(
                target=self._worker_loop, name="order-worker", daemon=True
            )
            self._worker.start()

    def stop_worker(self) -> bool:
        """Stop the worker and return True when it actually exited."""
        self._stop_event.set()
        worker = self._worker
        if worker is not None:
            worker.join(timeout=10)
        return worker is None or not worker.is_alive()

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            if self.is_paused():
                self._stop_event.wait(0.2)
                continue
            try:
                order_id = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.process_order(order_id)
            except Exception as exc:  # keep the worker alive
                self._last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("处理订单 %s 时发生未预期异常", order_id)

    # --------------------------------------------------------------- controls
    @property
    def paused(self) -> bool:
        with self._state_lock:
            return self._paused

    @property
    def pause_reason(self) -> str:
        with self._state_lock:
            return self._pause_reason

    def is_paused(self) -> bool:
        return self.paused

    def pause(self, reason: str = "") -> None:
        with self._state_lock:
            self._paused = True
            self._pause_reason = reason.strip()
            self.store.meta_set("paused", "1")
            self.store.meta_set("pause_reason", self._pause_reason)
        logger.warning("订单执行已暂停：%s", reason or "(未填写原因)")

    def resume(self) -> None:
        with self._state_lock:
            self._paused = False
            self._pause_reason = ""
            self.store.meta_set("paused", "0")
            self.store.meta_set("pause_reason", "")
        logger.warning("订单执行已恢复")

    # ------------------------------------------------------------------ intake
    def submit(self, request: OrderRequest) -> tuple[Order, bool]:
        """Persist and enqueue an order.

        Returns ``(order, created)``.  A repeated ``client_order_id`` returns the
        existing order with ``created=False`` and is never executed twice.
        """
        now = self._now_fn()
        client_order_id = request.client_order_id or new_client_order_id(
            request.symbol, request.side
        )
        timestamp = now.isoformat(timespec="seconds")
        order = Order(
            id=new_order_id(),
            client_order_id=client_order_id,
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            price=request.effective_price,
            quantity=request.quantity,
            status=OrderStatus.RECEIVED,
            broker_order_id=None,
            filled_quantity=0,
            filled_price=None,
            message=None,
            source=request.source or "api",
            remark=request.remark,
            prev_close=request.prev_close,
            trading_date=trading_date_of(now),
            created_at=timestamp,
            updated_at=timestamp,
            submitted_at=None,
            raw=request.model_dump(mode="json"),
        )
        created = self.store.create_order(order)
        if not created:
            existing = self.store.get_by_client_order_id(client_order_id)
            if existing is None:
                raise ConflictError("client_order_id 冲突，请更换后重试")
            return Order.from_mapping(existing), False
        self.store.audit(order.id, "received", {"request": order.raw})
        self._queue.put(order.id)
        return order, True

    # -------------------------------------------------------------- execution
    def process_pending(self) -> list[Order | None]:
        """Drain the in-memory queue synchronously (used by tests and demos)."""
        results: list[Order | None] = []
        while not self.is_paused():
            try:
                order_id = self._queue.get_nowait()
            except queue.Empty:
                break
            results.append(self.process_order(order_id))
        return results

    def process_order(self, order_id: str) -> Order | None:
        row = self.store.get(order_id)
        if row is None:
            return None
        order = Order.from_mapping(row)
        if order.status not in {OrderStatus.RECEIVED, OrderStatus.ACCEPTED}:
            return order
        if self.is_paused():
            self._queue.put(order.id)
            return order

        today = self._now_fn().date().isoformat()
        if order.trading_date != today:
            message = (
                f"订单日期 {order.trading_date} 已过期，未跨日执行；"
                "请重新提交当天的派单"
            )
            self.store.transition(
                order.id,
                [OrderStatus.RECEIVED.value, OrderStatus.ACCEPTED.value],
                OrderStatus.REJECTED.value,
                message=message,
            )
            self.store.audit(order.id, "expired", {"message": message})
            return self.get_order(order.id)

        account = None
        if self.config.trading.enforce_account_check:
            try:
                account = self.broker.get_account()
            except BrokerError as exc:
                message = f"下单前查询账户失败，未发送委托：{exc}"
                self._mark_failed(order, message, event="account_query_failed")
                return self.get_order(order.id)

        stats_row = self.store.daily_stats(order.trading_date)
        stats = DailyStats(count=int(stats_row["count"]), amount=float(stats_row["amount"]))
        decision = self._risk.validate(order, account, stats, now=self._now_fn())
        if not decision.approved:
            self.store.transition(
                order.id,
                [OrderStatus.RECEIVED.value, OrderStatus.ACCEPTED.value],
                OrderStatus.REJECTED.value,
                message=decision.reason,
            )
            self.store.audit(
                order.id,
                "risk_rejected",
                {
                    "reason": decision.reason,
                    "estimated_amount": decision.estimated_amount,
                    "estimated_fees": decision.estimated_fees,
                    "checks": [check.__dict__ for check in decision.checks],
                },
            )
            logger.info("订单 %s 被风控拒绝：%s", order.id, decision.reason)
            return self.get_order(order.id)

        if not self.store.transition(
            order.id,
            [OrderStatus.RECEIVED.value, OrderStatus.ACCEPTED.value],
            OrderStatus.ACCEPTED.value,
        ):
            return self.get_order(order.id)
        self.store.audit(
            order.id,
            "risk_approved",
            {
                "estimated_amount": decision.estimated_amount,
                "estimated_fees": decision.estimated_fees,
                "cash_required": decision.cash_required,
            },
        )

        if not self.store.transition(
            order.id, [OrderStatus.ACCEPTED.value], OrderStatus.SUBMITTING.value
        ):
            return self.get_order(order.id)
        self.store.audit(order.id, "submitting")

        try:
            result = self.broker.submit(order)
        except BrokerUnavailable as exc:
            self._mark_failed(order, f"券商不可用，未发送委托：{exc}", event="broker_unavailable")
            return self.get_order(order.id)
        except PermanentBrokerError as exc:
            self._mark_failed(order, str(exc), event="broker_rejected")
            return self.get_order(order.id)
        except UnknownSubmitOutcome as exc:
            message = str(exc)
            self.store.transition(
                order.id,
                [OrderStatus.SUBMITTING.value],
                OrderStatus.UNKNOWN.value,
                message=message,
            )
            self.store.audit(order.id, "submit_unknown", {"message": message})
            logger.error("订单 %s 提交结果未知：%s", order.id, message)
            return self.get_order(order.id)
        except Exception as exc:
            message = (
                f"提交委托时发生未预期异常：{type(exc).__name__}: {exc}。"
                "订单可能已报出，服务不会自动重试，请人工核对。"
            )
            self.store.transition(
                order.id,
                [OrderStatus.SUBMITTING.value],
                OrderStatus.UNKNOWN.value,
                message=message,
            )
            self.store.audit(order.id, "submit_unknown", {"message": message})
            logger.exception("订单 %s 提交异常", order.id)
            return self.get_order(order.id)

        self.store.transition(
            order.id,
            [OrderStatus.SUBMITTING.value],
            result.status.value,
            broker_order_id=result.broker_order_id,
            filled_quantity=result.filled_quantity,
            filled_price=result.filled_price,
            message=result.message,
            submitted_at=self._now_fn().isoformat(timespec="seconds"),
        )
        self.store.audit(
            order.id,
            "broker_result",
            {
                "broker_order_id": result.broker_order_id,
                "status": result.status.value,
                "filled_quantity": result.filled_quantity,
                "filled_price": result.filled_price,
                "message": result.message,
            },
        )
        return self.get_order(order.id)

    def _mark_failed(self, order: Order, message: str, *, event: str) -> None:
        self.store.transition(
            order.id,
            [
                OrderStatus.RECEIVED.value,
                OrderStatus.ACCEPTED.value,
                OrderStatus.SUBMITTING.value,
            ],
            OrderStatus.FAILED.value,
            message=message,
        )
        self.store.audit(order.id, event, {"message": message})
        logger.error("订单 %s 失败：%s", order.id, message)

    # ------------------------------------------------------------------ cancel
    def cancel(self, order_id: str, reason: str = "") -> Order:
        order = self.get_order(order_id)
        if order is None:
            raise NotFoundError(f"订单不存在：{order_id}")
        if order.status in {OrderStatus.RECEIVED, OrderStatus.ACCEPTED}:
            if not self.store.transition(
                order.id,
                [OrderStatus.RECEIVED.value, OrderStatus.ACCEPTED.value],
                OrderStatus.CANCELED.value,
                message=reason or "用户撤单",
            ):
                raise ConflictError("订单状态已变化，撤单未生效")
            self.store.audit(order.id, "canceled", {"reason": reason})
            return self._require_order(order.id)

        if order.status is OrderStatus.CANCEL_PENDING:
            raise ConflictError("撤单请求已发送，等待券商确认")
        if order.status in {OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED}:
            if not order.broker_order_id:
                raise ConflictError("订单没有券商委托编号，无法撤单")
            result = self.broker.cancel(order.broker_order_id)
            if not result.success:
                raise ConflictError(result.message or "券商拒绝撤单")
            self.store.transition(
                order.id,
                [OrderStatus.SUBMITTED.value, OrderStatus.PARTIALLY_FILLED.value],
                OrderStatus.CANCEL_PENDING.value,
                message=result.message or "撤单请求已发送",
            )
            self.store.audit(
                order.id, "cancel_requested", {"message": result.message}
            )
            return self._require_order(order.id)
        raise ConflictError(f"当前状态 {order.status.value} 不能撤单")

    # ----------------------------------------------------------------- queries
    def get_order(self, order_id: str) -> Order | None:
        row = self.store.get(order_id)
        return Order.from_mapping(row) if row else None

    def _require_order(self, order_id: str) -> Order:
        order = self.get_order(order_id)
        if order is None:
            raise NotFoundError(f"订单不存在：{order_id}")
        return order

    def list_orders(self, status: str | None = None, limit: int = 100) -> list[Order]:
        return [Order.from_mapping(row) for row in self.store.list_orders(status, limit)]

    def events(self, order_id: str) -> list[dict[str, Any]]:
        if self.store.get(order_id) is None:
            raise NotFoundError(f"订单不存在：{order_id}")
        return self.store.events(order_id)

    def account(self):
        return self.broker.get_account()

    def status(self) -> dict[str, Any]:
        return {
            "paused": self.is_paused(),
            "pause_reason": self.pause_reason,
            "queue_size": self._queue.qsize(),
            "broker": self.broker.name,
            "dry_run": self.config.trading.dry_run,
            "counts": self.store.counts(),
            "last_error": self._last_error,
        }
