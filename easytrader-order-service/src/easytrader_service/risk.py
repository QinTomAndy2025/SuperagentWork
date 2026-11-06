"""Pre-trade risk checks.

Risk decisions are deliberately conservative: when the account snapshot is
missing or a limit is unknown, the order is rejected rather than sent to the
broker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .broker.base import AccountSnapshot
from .config import AppConfig
from .fees import FeeModel
from .models import Order, Side, price_band_for
from .trading_calendar import TradingCalendar


@dataclass(frozen=True)
class DailyStats:
    count: int
    amount: float


@dataclass(frozen=True)
class RiskCheck:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    checks: list[RiskCheck] = field(default_factory=list)
    estimated_amount: float = 0.0
    estimated_fees: float = 0.0
    cash_required: float = 0.0


class RiskEngine:
    def __init__(
        self,
        config: AppConfig,
        *,
        calendar: TradingCalendar | None = None,
        fees: FeeModel | None = None,
    ):
        self._config = config
        self._calendar = calendar or TradingCalendar(config.trading)
        self._fees = fees or FeeModel(config.fees)

    def validate(
        self,
        order: Order,
        account: AccountSnapshot | None,
        stats: DailyStats,
        now: datetime | None = None,
    ) -> RiskDecision:
        trading = self._config.trading
        checks: list[RiskCheck] = []

        def check(name: str, passed: bool, detail: str = "") -> None:
            checks.append(RiskCheck(name=name, passed=passed, detail=detail))

        amount = round(float(order.price) * int(order.quantity), 2)
        fees = self._fees.estimate(order.side, order.price, order.quantity)
        cash_required = self._fees.cash_required(order.side, order.price, order.quantity)

        # 1. Market session
        if trading.enforce_trading_session:
            is_open, session_reason = self._calendar.is_open(now)
            check("trading_session", is_open, session_reason if not is_open else "交易时段内")
        else:
            check("trading_session", True, "未启用交易时段检查")

        # 2. Symbol allow/block lists
        if trading.allowed_symbols:
            allowed = order.symbol in trading.allowed_symbols
            check(
                "symbol_allowlist",
                allowed,
                "代码不在允许列表内" if not allowed else "代码在允许列表内",
            )
        else:
            check("symbol_allowlist", True, "未配置允许列表")

        blocked = order.symbol in trading.blocked_symbols
        check("symbol_blocklist", not blocked, "代码在禁止列表内" if blocked else "代码未被禁止")

        # 3. Quantity / price shape
        step_ok = order.quantity > 0 and order.quantity % trading.quantity_step == 0
        check(
            "quantity_step",
            step_ok,
            f"数量必须是 {trading.quantity_step} 的正整数倍" if not step_ok else "数量符合最小单位",
        )
        price_ok = order.price > 0
        check("price_positive", price_ok, "价格必须大于 0" if not price_ok else "价格大于 0")

        # 4. Per-order and daily limits
        order_limit_ok = amount <= trading.max_order_amount + 1e-9
        check(
            "max_order_amount",
            order_limit_ok,
            (
                f"单笔金额 {amount:.2f} 超过上限 {trading.max_order_amount:.2f}"
                if not order_limit_ok
                else "单笔金额在上限内"
            ),
        )

        if trading.max_daily_orders > 0:
            daily_count_ok = stats.count < trading.max_daily_orders
            check(
                "max_daily_orders",
                daily_count_ok,
                (
                    f"当日已受理 {stats.count} 笔，达到笔数上限 {trading.max_daily_orders}"
                    if not daily_count_ok
                    else "当日委托笔数在上限内"
                ),
            )
        else:
            check("max_daily_orders", True, "未配置每日笔数上限")

        if trading.max_daily_amount > 0:
            daily_amount_ok = stats.amount + amount <= trading.max_daily_amount + 1e-9
            check(
                "max_daily_amount",
                daily_amount_ok,
                (
                    f"当日金额 {stats.amount:.2f} + 本笔 {amount:.2f} "
                    f"超过上限 {trading.max_daily_amount:.2f}"
                    if not daily_amount_ok
                    else "当日金额在上限内"
                ),
            )
        else:
            check("max_daily_amount", True, "未配置每日金额上限")

        # 5. Optional price band (requires the dispatch source to supply prev_close)
        if trading.price_band_check:
            if order.prev_close is None:
                check("price_band", True, "未提供昨收价，跳过涨跌停检查")
            else:
                pct = price_band_for(order.symbol, trading.price_band_pct)
                lower = round(order.prev_close * (1 - pct), 4)
                upper = round(order.prev_close * (1 + pct), 4)
                in_band = lower <= order.price <= upper
                check(
                    "price_band",
                    in_band,
                    (
                        f"价格 {order.price} 超出 {lower}-{upper} 的涨跌停区间"
                        if not in_band
                        else "价格在涨跌停区间内"
                    ),
                )
        else:
            check("price_band", True, "未启用涨跌停检查")

        # 6. Account checks
        if trading.enforce_account_check:
            if account is None:
                check("account", False, "无法获取账户资金/持仓，禁止下单")
            elif order.side is Side.BUY:
                if account.cash_available is None:
                    check("account", False, "券商未返回可用资金，禁止买入")
                else:
                    enough = account.cash_available + 1e-9 >= cash_required
                    check(
                        "account",
                        enough,
                        (
                            f"可用资金 {account.cash_available:.2f} 不足，"
                            f"本笔需要 {cash_required:.2f}（含费用）"
                            if not enough
                            else "可用资金充足"
                        ),
                    )
            else:
                position = next(
                    (item for item in account.positions if item.symbol == order.symbol),
                    None,
                )
                if position is None:
                    check("account", False, f"账户没有 {order.symbol} 持仓，禁止卖出")
                else:
                    enough = position.available_quantity >= order.quantity
                    check(
                        "account",
                        enough,
                        (
                            f"可卖数量 {position.available_quantity} 不足，"
                            f"需要 {order.quantity}"
                            if not enough
                            else "可卖数量充足"
                        ),
                    )
        else:
            check("account", True, "未启用账户资金/持仓检查")

        approved = all(item.passed for item in checks)
        reason = ""
        if not approved:
            failed = next(item for item in checks if not item.passed)
            reason = failed.detail or failed.name
        return RiskDecision(
            approved=approved,
            reason=reason,
            checks=checks,
            estimated_amount=amount,
            estimated_fees=fees,
            cash_required=cash_required,
        )
