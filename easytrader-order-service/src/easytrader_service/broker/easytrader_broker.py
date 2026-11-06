"""easytrader adapter.

The adapter deliberately imports easytrader lazily: easytrader drives Windows
GUI clients through pywinauto and cannot be imported on Linux/macOS.  Tests
inject a fake module object, and paper mode never imports it at all.
"""

from __future__ import annotations

import re
import sys
import threading
from typing import Any

from ..config import EasytraderConfig
from ..errors import (
    BrokerUnavailable,
    PermanentBrokerError,
    TransientBrokerError,
    UnknownSubmitOutcome,
)
from ..models import Order, OrderStatus, Side
from ..timeutil import iso_now
from .base import AccountSnapshot, CancelResult, Position, SubmitResult

_PERMANENT_EXCEPTION_NAMES = {"TradeError", "NotLoginError"}
_FAILURE_WORDS = ("失败", "错误", "废单", "拒绝", "不能", "不支持", "异常")
_CASH_KEYS = (
    "可用金额",
    "可用资金",
    "可用余额",
    "enable_balance",
    "cash",
    "current_balance",
    "资金余额",
    "可用",
)
_TOTAL_KEYS = ("总资产", "asset_balance", "total_asset", "总资产值")
_SYMBOL_KEYS = ("证券代码", "security", "stock_code", "code", "stock_id")
_QUANTITY_KEYS = ("股票余额", "当前持仓", "持仓数量", "volume", "current_amount", "持仓")
_AVAILABLE_KEYS = (
    "可用余额",
    "可用数量",
    "可卖数量",
    "can_use_volume",
    "enable_amount",
    "available",
    "可用",
)
_COST_KEYS = ("成本价", "成本价格", "open_price", "avg_price", "cost_price")
_PRICE_KEYS = ("市价", "当前价", "现价", "last_price", "price")
_MESSAGE_KEYS = ("message", "msg", "错误信息", "error_msg", "提示")
_ENTRUST_KEYS = ("entrust_no", "order_id", "合同编号", "委托编号")
_ERROR_CODE_KEYS = ("error_no", "error_id", "error_code")


def _first_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, dict):
                return item
    return {}


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("，", "")
    if text in {"", "-", "--", "None", "nan", "NaN"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    return int(number) if number is not None else None


def _pick(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping:
            value = mapping[key]
            if value is None:
                continue
            if isinstance(value, str) and value.strip() in {"", "-", "--"}:
                continue
            return value
    return None


def _extract_symbol(mapping: dict[str, Any]) -> str | None:
    for key in _SYMBOL_KEYS:
        value = mapping.get(key)
        if value is None:
            continue
        match = re.search(r"\d{6}", str(value))
        if match:
            return match.group(0)
    return None


def _normalise_positions(raw: Any) -> list[Position]:
    if isinstance(raw, dict):
        rows: list[Any] = list(raw.values())
    elif isinstance(raw, (list, tuple)):
        rows = list(raw)
    else:
        rows = []
    positions: list[Position] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = _extract_symbol(row)
        if symbol is None:
            continue
        quantity = _to_int(_pick(row, _QUANTITY_KEYS))
        available = _to_int(_pick(row, _AVAILABLE_KEYS))
        if quantity is None:
            quantity = available
        if quantity is None or quantity <= 0:
            continue
        if available is None:
            # The broker client does not expose sellable quantity; the broker
            # itself will still reject an invalid sell.  Be conservative in
            # the service risk check by treating it as the full position.
            available = quantity
        positions.append(
            Position(
                symbol=symbol,
                quantity=int(quantity),
                available_quantity=int(max(0, min(available, quantity))),
                cost_price=_to_float(_pick(row, _COST_KEYS)),
                market_price=_to_float(_pick(row, _PRICE_KEYS)),
            )
        )
    return positions


class EasytraderBroker:
    """Thin lock-protected wrapper around an easytrader user object."""

    def __init__(
        self,
        config: EasytraderConfig,
        *,
        easytrader_module: Any | None = None,
    ):
        self._config = config
        self._module = easytrader_module
        self._user: Any | None = None
        self._lock = threading.RLock()

    @property
    def name(self) -> str:
        return f"easytrader:{self._config.broker}"

    # --------------------------------------------------------------- connect
    def _import_easytrader(self) -> Any:
        if sys.platform != "win32":
            raise BrokerUnavailable(
                "easytrader 的券商客户端接入依赖 Windows GUI 自动化，当前系统不是 Windows。"
                "请把服务运行在安装了券商客户端的 Windows 主机上，或使用 broker.type=\"paper\"。"
            )
        try:
            import easytrader  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover - platform-specific
            raise BrokerUnavailable(
                f"无法导入 easytrader：{exc}。请先执行 pip install \"easytrader-order-service[live]\""
            ) from exc
        return easytrader

    def connect(self) -> None:
        with self._lock:
            if self._user is not None:
                return
            module = self._module if self._module is not None else self._import_easytrader()
            broker = self._config.broker
            try:
                user = module.use(broker)
                if broker.lower() in {"xq", "雪球"}:
                    if not self._config.prepare:
                        raise BrokerUnavailable("雪球模式需要配置 broker.easytrader.prepare 文件")
                    user.prepare(self._config.prepare)
                elif broker.lower() == "miniqmt":
                    user.connect(**self._config.connect_kwargs)
                elif self._config.exe_path:
                    user.connect(self._config.exe_path, **self._config.connect_kwargs)
                else:
                    user.connect(**self._config.connect_kwargs)
            except BrokerUnavailable:
                raise
            except Exception as exc:
                raise BrokerUnavailable(
                    f"连接 easytrader 券商客户端失败：{exc}。"
                    "请确认客户端已启动并已登录，且 exe_path 指向 xiadan.exe。"
                ) from exc
            self._user = user

    def _require_user(self) -> Any:
        if self._user is None:
            raise BrokerUnavailable("easytrader 尚未连接，请先调用 connect()")
        return self._user

    # ---------------------------------------------------------------- account
    def get_account(self) -> AccountSnapshot:
        with self._lock:
            user = self._require_user()
            errors: list[str] = []
            balance: Any = None
            positions: Any = None
            try:
                balance = user.balance
            except Exception as exc:
                errors.append(f"查询资金失败：{exc}")
            try:
                positions = user.position
            except Exception as exc:
                errors.append(f"查询持仓失败：{exc}")
            if balance is None and positions is None:
                raise TransientBrokerError("；".join(errors) or "券商账户查询失败")

            balance_row = _first_mapping(balance)
            return AccountSnapshot(
                cash_available=_to_float(_pick(balance_row, _CASH_KEYS)),
                total_assets=_to_float(_pick(balance_row, _TOTAL_KEYS)),
                positions=_normalise_positions(positions),
                fetched_at=iso_now(),
                raw={},
            )

    # ----------------------------------------------------------------- submit
    def submit(self, order: Order) -> SubmitResult:
        with self._lock:
            user = self._require_user()
            try:
                result = self._submit_locked(user, order)
            except Exception as exc:
                name = type(exc).__name__
                if name in _PERMANENT_EXCEPTION_NAMES or isinstance(
                    exc, (ValueError, TypeError, KeyError)
                ):
                    raise PermanentBrokerError(f"券商拒绝委托：{exc}") from exc
                raise UnknownSubmitOutcome(
                    f"提交委托时发生异常：{name}: {exc}。"
                    "订单可能已经报出，服务不会自动重试，请人工核对券商委托列表。"
                ) from exc
            return self._normalise_submit(result)

    def _submit_locked(self, user: Any, order: Order) -> Any:
        if order.order_type.value == "limit":
            if order.side is Side.BUY:
                return user.buy(order.symbol, price=order.price, amount=order.quantity)
            return user.sell(order.symbol, price=order.price, amount=order.quantity)

        ttype = self._config.market_ttype
        kwargs = {"ttype": ttype} if ttype else {}
        if order.side is Side.BUY:
            return user.market_buy(order.symbol, amount=order.quantity, **kwargs)
        return user.market_sell(order.symbol, amount=order.quantity, **kwargs)

    def _normalise_submit(self, result: Any) -> SubmitResult:
        if isinstance(result, bool):
            if result:
                return SubmitResult(None, OrderStatus.SUBMITTED, message="券商返回成功")
            raise PermanentBrokerError("券商返回失败")
        if isinstance(result, int):
            if result > 0:
                return SubmitResult(str(result), OrderStatus.SUBMITTED, message="委托已提交")
            raise PermanentBrokerError(f"券商返回委托编号 {result}，委托失败")
        if isinstance(result, str):
            text = result.strip()
            if text:
                return SubmitResult(text, OrderStatus.SUBMITTED, message="委托已提交")
            raise UnknownSubmitOutcome("券商返回了空字符串，无法判断委托结果")

        rows = result if isinstance(result, (list, tuple)) else [result]
        message: str | None = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key in _ERROR_CODE_KEYS:
                code = _to_int(row.get(key))
                if code not in (None, 0):
                    raise PermanentBrokerError(f"券商返回错误码 {code}：{row}")
            entrust = _pick(row, _ENTRUST_KEYS)
            if entrust is not None:
                entrust_int = _to_int(entrust)
                if entrust_int is not None and entrust_int <= 0:
                    raise PermanentBrokerError(f"券商返回无效委托编号 {entrust}，委托失败")
                return SubmitResult(
                    str(entrust),
                    OrderStatus.SUBMITTED,
                    message=str(_pick(row, _MESSAGE_KEYS) or "委托已提交"),
                )
            row_message = _pick(row, _MESSAGE_KEYS)
            if row_message:
                message = str(row_message)
                if any(word in message for word in _FAILURE_WORDS):
                    raise PermanentBrokerError(message)
        if message:
            return SubmitResult(None, OrderStatus.SUBMITTED, message=message)
        raise UnknownSubmitOutcome(f"无法识别券商返回结果：{result!r}")

    # ----------------------------------------------------------------- cancel
    def cancel(self, broker_order_id: str) -> CancelResult:
        with self._lock:
            user = self._require_user()
            if self._config.broker.lower() == "miniqmt":
                target: Any = int(broker_order_id) if str(broker_order_id).isdigit() else broker_order_id
            else:
                # ClientTrader compares the grid's string entrust number with
                # the value passed here, so keep it a string.
                target = str(broker_order_id)
            try:
                result = user.cancel_entrust(target)
            except Exception as exc:
                raise PermanentBrokerError(f"撤单失败：{exc}") from exc
            message = self._message_from(result) or "撤单请求已发送"
            if any(word in message for word in _FAILURE_WORDS):
                raise PermanentBrokerError(message)
            return CancelResult(True, message)

    @staticmethod
    def _message_from(result: Any) -> str:
        if isinstance(result, dict):
            value = _pick(result, _MESSAGE_KEYS)
            return str(value) if value is not None else ""
        if isinstance(result, (list, tuple)):
            for row in result:
                if isinstance(row, dict):
                    value = _pick(row, _MESSAGE_KEYS)
                    if value is not None:
                        return str(value)
        if result is None:
            return ""
        return str(result)

    def close(self) -> None:
        # Never kill the broker GUI on shutdown; the user may be using it.
        return None
