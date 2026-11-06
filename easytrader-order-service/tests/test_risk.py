from __future__ import annotations

from datetime import datetime

from easytrader_service.broker.base import AccountSnapshot, Position
from easytrader_service.config import AppConfig
from easytrader_service.models import Side
from easytrader_service.risk import DailyStats, RiskEngine
from easytrader_service.timeutil import CHINA_TZ


def risk_config(base: AppConfig, **trading_overrides) -> AppConfig:
    data = base.model_dump()
    data["trading"].update(trading_overrides)
    return AppConfig.model_validate(data)


def account(cash: float | None = 100_000.0, positions: list[Position] | None = None):
    return AccountSnapshot(
        cash_available=cash,
        total_assets=cash,
        positions=positions or [],
        fetched_at="2026-11-06T10:00:00+08:00",
    )


def position(symbol="000001", quantity=100, available=100):
    return Position(
        symbol=symbol,
        quantity=quantity,
        available_quantity=available,
        cost_price=10.0,
        market_price=10.0,
    )


def test_approved_buy(config, order_factory):
    engine = RiskEngine(risk_config(config))
    decision = engine.validate(
        order_factory(price=10.0, quantity=100), account(), DailyStats(0, 0.0)
    )
    assert decision.approved, decision.reason
    assert decision.estimated_amount == 1000.0
    assert decision.cash_required > 1000.0


def test_session_closed_on_weekend(config, order_factory):
    engine = RiskEngine(risk_config(config, enforce_trading_session=True))
    saturday = datetime(2026, 11, 7, 10, 0, tzinfo=CHINA_TZ)
    decision = engine.validate(
        order_factory(), account(), DailyStats(0, 0.0), now=saturday
    )
    assert not decision.approved
    assert "交易日" in decision.reason or "交易时段" in decision.reason


def test_session_closed_on_holiday(config, order_factory):
    engine = RiskEngine(
        risk_config(config, enforce_trading_session=True, holidays=["2026-11-06"])
    )
    friday = datetime(2026, 11, 6, 10, 0, tzinfo=CHINA_TZ)
    decision = engine.validate(
        order_factory(), account(), DailyStats(0, 0.0), now=friday
    )
    assert not decision.approved
    assert "节假日" in decision.reason


def test_session_closed_at_lunch(config, order_factory):
    engine = RiskEngine(risk_config(config, enforce_trading_session=True))
    lunch = datetime(2026, 11, 6, 12, 0, tzinfo=CHINA_TZ)
    decision = engine.validate(
        order_factory(), account(), DailyStats(0, 0.0), now=lunch
    )
    assert not decision.approved
    assert "交易时段" in decision.reason


def test_insufficient_cash_is_rejected(config, order_factory):
    engine = RiskEngine(risk_config(config))
    decision = engine.validate(
        order_factory(price=10.0, quantity=100), account(cash=100.0), DailyStats(0, 0.0)
    )
    assert not decision.approved
    assert "可用资金" in decision.reason


def test_missing_account_is_rejected(config, order_factory):
    engine = RiskEngine(risk_config(config))
    decision = engine.validate(order_factory(), None, DailyStats(0, 0.0))
    assert not decision.approved
    assert "账户" in decision.reason


def test_sell_without_position_is_rejected(config, order_factory):
    engine = RiskEngine(risk_config(config))
    order = order_factory(side=Side.SELL)
    decision = engine.validate(order, account(), DailyStats(0, 0.0))
    assert not decision.approved
    assert "持仓" in decision.reason


def test_sell_with_t_plus_one_unavailable_is_rejected(config, order_factory):
    engine = RiskEngine(risk_config(config))
    order = order_factory(side=Side.SELL)
    decision = engine.validate(
        order, account(positions=[position(available=0)]), DailyStats(0, 0.0)
    )
    assert not decision.approved
    assert "可卖数量" in decision.reason


def test_sell_with_available_position_is_approved(config, order_factory):
    engine = RiskEngine(risk_config(config))
    order = order_factory(side=Side.SELL)
    decision = engine.validate(
        order, account(positions=[position()]), DailyStats(0, 0.0)
    )
    assert decision.approved, decision.reason


def test_quantity_must_follow_step(config, order_factory):
    engine = RiskEngine(risk_config(config, quantity_step=100))
    decision = engine.validate(
        order_factory(quantity=150), account(), DailyStats(0, 0.0)
    )
    assert not decision.approved
    assert "100" in decision.reason


def test_max_order_amount(config, order_factory):
    engine = RiskEngine(risk_config(config, max_order_amount=500.0))
    decision = engine.validate(
        order_factory(price=10.0, quantity=100), account(), DailyStats(0, 0.0)
    )
    assert not decision.approved
    assert "单笔金额" in decision.reason


def test_max_daily_orders(config, order_factory):
    engine = RiskEngine(risk_config(config, max_daily_orders=2))
    decision = engine.validate(
        order_factory(), account(), DailyStats(count=2, amount=2000.0)
    )
    assert not decision.approved
    assert "笔数" in decision.reason


def test_max_daily_amount(config, order_factory):
    engine = RiskEngine(risk_config(config, max_daily_amount=1500.0))
    decision = engine.validate(
        order_factory(price=10.0, quantity=100), account(), DailyStats(0, 1000.0)
    )
    assert not decision.approved
    assert "当日金额" in decision.reason


def test_blocked_symbol(config, order_factory):
    engine = RiskEngine(risk_config(config, blocked_symbols=["000001"]))
    decision = engine.validate(order_factory(), account(), DailyStats(0, 0.0))
    assert not decision.approved
    assert "禁止" in decision.reason


def test_allowlist_symbol(config, order_factory):
    engine = RiskEngine(risk_config(config, allowed_symbols=["600000"]))
    decision = engine.validate(order_factory(), account(), DailyStats(0, 0.0))
    assert not decision.approved
    assert "允许列表" in decision.reason


def test_price_band_rejects_out_of_range(config, order_factory):
    engine = RiskEngine(risk_config(config, price_band_check=True))
    order = order_factory(price=12.0, prev_close=10.0)
    decision = engine.validate(order, account(), DailyStats(0, 0.0))
    assert not decision.approved
    assert "涨跌停" in decision.reason


def test_price_band_accepts_in_range(config, order_factory):
    engine = RiskEngine(risk_config(config, price_band_check=True))
    order = order_factory(price=10.9, prev_close=10.0)
    decision = engine.validate(order, account(), DailyStats(0, 0.0))
    assert decision.approved, decision.reason
