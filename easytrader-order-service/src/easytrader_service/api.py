"""FastAPI application exposing order intake and control endpoints."""

from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .broker import Broker, build_broker
from .broker.base import AccountSnapshot
from .config import AppConfig
from .errors import BrokerError, BrokerUnavailable, ConflictError, NotFoundError
from .models import Order, OrderRequest, OrderStatus
from .service import OrderService
from .store import OrderStore


# --------------------------------------------------------------------- schemas
class OrderOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    price: float
    quantity: int
    status: str
    broker_order_id: str | None = None
    filled_quantity: int = 0
    filled_price: float | None = None
    message: str | None = None
    source: str = ""
    remark: str = ""
    prev_close: float | None = None
    trading_date: str
    created_at: str
    updated_at: str
    submitted_at: str | None = None

    @classmethod
    def from_order(cls, order: Order) -> "OrderOut":
        return cls(**order.as_dict())


class OrderSubmitResponse(BaseModel):
    order: OrderOut
    duplicate: bool


class CancelRequest(BaseModel):
    reason: str = Field(default="", max_length=200)


class PositionOut(BaseModel):
    symbol: str
    quantity: int
    available_quantity: int
    cost_price: float | None = None
    market_price: float | None = None


class AccountOut(BaseModel):
    cash_available: float | None
    total_assets: float | None
    positions: list[PositionOut]
    fetched_at: str

    @classmethod
    def from_snapshot(cls, snapshot: AccountSnapshot) -> "AccountOut":
        return cls(
            cash_available=snapshot.cash_available,
            total_assets=snapshot.total_assets,
            positions=[
                PositionOut(
                    symbol=item.symbol,
                    quantity=item.quantity,
                    available_quantity=item.available_quantity,
                    cost_price=item.cost_price,
                    market_price=item.market_price,
                )
                for item in snapshot.positions
            ],
            fetched_at=snapshot.fetched_at,
        )


class EventOut(BaseModel):
    id: int
    order_id: str
    event: str
    detail: dict[str, Any]
    at: str


class PauseRequest(BaseModel):
    paused: bool = True
    reason: str = Field(default="", max_length=200)


class ControlStatusOut(BaseModel):
    paused: bool
    pause_reason: str
    queue_size: int
    broker: str
    dry_run: bool
    counts: dict[str, int]
    last_error: str | None = None


class HealthOut(BaseModel):
    status: str
    version: str
    broker: str
    paused: bool
    dry_run: bool


# ------------------------------------------------------------------- utilities
def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _extract_api_key(request: Request) -> str | None:
    key = request.headers.get("x-api-key")
    if key:
        return key.strip()
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def require_api_key(request: Request) -> None:
    expected = request.app.state.config.server.api_key
    provided = _extract_api_key(request)
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=401,
            detail="API key 无效或缺失，请使用 X-API-Key 或 Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _not_found(order_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"订单不存在：{order_id}")


# ------------------------------------------------------------------------- app
def create_app(
    config: AppConfig,
    *,
    broker: Broker | None = None,
    start_worker: bool = True,
) -> FastAPI:
    config.validate_runtime()
    configure_logging(config.server.log_level)

    store = OrderStore(config.database.path)
    try:
        actual_broker = broker if broker is not None else build_broker(config)
    except Exception:
        store.close()
        raise
    service = OrderService(config, store, actual_broker)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service.initialize()
        if start_worker:
            service.start_worker()
        try:
            yield
        finally:
            stopped = service.stop_worker()
            if stopped:
                actual_broker.close()
                store.close()
            else:
                logging.getLogger(__name__).warning(
                    "worker 未能在超时内退出，保留数据库/券商连接由进程退出时释放"
                )

    app = FastAPI(
        title="easytrader order service",
        version=__version__,
        description="Local order-dispatch intake and automated A-share order execution.",
        lifespan=lifespan,
        docs_url="/docs" if config.server.enable_docs else None,
        redoc_url=None,
    )
    app.state.config = config
    app.state.store = store
    app.state.broker = actual_broker
    app.state.service = service

    @app.get("/api/v1/health", response_model=HealthOut)
    def health(request: Request) -> HealthOut:
        svc: OrderService = request.app.state.service
        return HealthOut(
            status="ok",
            version=__version__,
            broker=svc.broker.name,
            paused=svc.is_paused(),
            dry_run=config.trading.dry_run,
        )

    router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])

    @router.post("/orders", response_model=OrderSubmitResponse, status_code=202)
    def submit_order(payload: OrderRequest, request: Request) -> OrderSubmitResponse:
        svc: OrderService = request.app.state.service
        try:
            order, created = svc.submit(payload)
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return OrderSubmitResponse(order=OrderOut.from_order(order), duplicate=not created)

    @router.get("/orders", response_model=list[OrderOut])
    def list_orders(
        request: Request,
        status: OrderStatus | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[OrderOut]:
        svc: OrderService = request.app.state.service
        status_value = status.value if status is not None else None
        return [OrderOut.from_order(order) for order in svc.list_orders(status_value, limit)]

    @router.get("/orders/{order_id}", response_model=OrderOut)
    def get_order(order_id: str, request: Request) -> OrderOut:
        svc: OrderService = request.app.state.service
        order = svc.get_order(order_id)
        if order is None:
            raise _not_found(order_id)
        return OrderOut.from_order(order)

    @router.get("/orders/{order_id}/events", response_model=list[EventOut])
    def get_events(order_id: str, request: Request) -> list[EventOut]:
        svc: OrderService = request.app.state.service
        try:
            events = svc.events(order_id)
        except NotFoundError as exc:
            raise _not_found(order_id) from exc
        return [EventOut(**event) for event in events]

    @router.post("/orders/{order_id}/cancel", response_model=OrderOut)
    def cancel_order(
        order_id: str,
        request: Request,
        payload: CancelRequest | None = None,
    ) -> OrderOut:
        svc: OrderService = request.app.state.service
        try:
            order = svc.cancel(order_id, payload.reason if payload else "")
        except NotFoundError as exc:
            raise _not_found(order_id) from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BrokerError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return OrderOut.from_order(order)

    @router.get("/account", response_model=AccountOut)
    def get_account(request: Request) -> AccountOut:
        svc: OrderService = request.app.state.service
        try:
            snapshot = svc.account()
        except BrokerUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except BrokerError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return AccountOut.from_snapshot(snapshot)

    @router.post("/control/pause", response_model=ControlStatusOut)
    def set_pause(payload: PauseRequest, request: Request) -> ControlStatusOut:
        svc: OrderService = request.app.state.service
        if payload.paused:
            svc.pause(payload.reason)
        else:
            svc.resume()
        return ControlStatusOut(**svc.status())

    @router.get("/control/status", response_model=ControlStatusOut)
    def control_status(request: Request) -> ControlStatusOut:
        svc: OrderService = request.app.state.service
        return ControlStatusOut(**svc.status())

    app.include_router(router)
    return app
