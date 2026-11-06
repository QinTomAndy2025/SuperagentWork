"""Configuration models and TOML loading."""

from __future__ import annotations

import os
import tomllib
from datetime import date, time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ConfigError
from .models import normalize_symbol

_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerConfig(_Model):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    api_key: str = ""
    enable_docs: bool = True
    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def _valid_log_level(cls, value: str) -> str:
        upper = value.strip().upper()
        if upper not in _LOG_LEVELS:
            raise ValueError(f"log_level 必须是 {sorted(_LOG_LEVELS)} 之一")
        return upper


class DatabaseConfig(_Model):
    path: str = "data/orders.db"


class FeeConfig(_Model):
    commission_rate: float = Field(default=0.00025, ge=0)
    min_commission: float = Field(default=5.0, ge=0)
    stamp_duty_rate: float = Field(default=0.0005, ge=0)
    transfer_fee_rate: float = Field(default=0.00001, ge=0)


class TradingConfig(_Model):
    # Two-key safety: paper requires dry_run=true, live requires dry_run=false
    # together with broker.type="easytrader".
    dry_run: bool = True
    allowed_symbols: list[str] = Field(default_factory=list)
    blocked_symbols: list[str] = Field(default_factory=list)
    max_order_amount: float = Field(default=10_000.0, gt=0)
    max_daily_amount: float = Field(default=50_000.0, ge=0)
    max_daily_orders: int = Field(default=20, ge=0)
    quantity_step: int = Field(default=100, gt=0)
    enforce_trading_session: bool = True
    sessions: list[tuple[str, str]] = Field(
        default_factory=lambda: [("09:30", "11:30"), ("13:00", "15:00")]
    )
    weekdays: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5])
    holidays: list[str] = Field(default_factory=list)
    enforce_account_check: bool = True
    price_band_check: bool = False
    price_band_pct: float = Field(default=0.10, gt=0, le=1)
    t_plus_one: bool = True

    @field_validator("allowed_symbols", "blocked_symbols")
    @classmethod
    def _normalise_symbols(cls, values: list[str]) -> list[str]:
        return [normalize_symbol(value) for value in values]

    @field_validator("sessions")
    @classmethod
    def _valid_sessions(cls, sessions: list[tuple[str, str]]) -> list[tuple[str, str]]:
        if not sessions:
            raise ValueError("sessions 不能为空")
        checked: list[tuple[str, str]] = []
        for start_raw, end_raw in sessions:
            try:
                start = time.fromisoformat(start_raw)
                end = time.fromisoformat(end_raw)
            except ValueError as exc:
                raise ValueError(f"无效的交易时段 {start_raw!r}-{end_raw!r}") from exc
            if start >= end:
                raise ValueError(f"交易时段开始时间必须早于结束时间：{start_raw}-{end_raw}")
            checked.append((start.strftime("%H:%M"), end.strftime("%H:%M")))
        return checked

    @field_validator("weekdays")
    @classmethod
    def _valid_weekdays(cls, weekdays: list[int]) -> list[int]:
        if not weekdays:
            raise ValueError("weekdays 不能为空")
        for day in weekdays:
            if day < 1 or day > 7:
                raise ValueError("weekdays 使用 1-7（周一至周日）")
        return sorted(set(weekdays))

    @field_validator("holidays")
    @classmethod
    def _valid_holidays(cls, holidays: list[str]) -> list[str]:
        checked: list[str] = []
        for raw in holidays:
            try:
                checked.append(date.fromisoformat(raw).isoformat())
            except ValueError as exc:
                raise ValueError(f"无效的节假日日期：{raw!r}，应为 YYYY-MM-DD") from exc
        return sorted(set(checked))


class PaperConfig(_Model):
    initial_cash: float = Field(default=100_000.0, ge=0)
    state_path: str = "data/paper_account.json"


class EasytraderConfig(_Model):
    broker: str = "ths"
    exe_path: str = ""
    prepare: str = ""
    connect_kwargs: dict[str, Any] = Field(default_factory=dict)
    market_ttype: str | None = None
    account_id: str = ""


class BrokerConfig(_Model):
    type: Literal["paper", "easytrader"] = "paper"
    paper: PaperConfig = Field(default_factory=PaperConfig)
    easytrader: EasytraderConfig = Field(default_factory=EasytraderConfig)


class AppConfig(_Model):
    server: ServerConfig = Field(default_factory=ServerConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    trading: TradingConfig = Field(default_factory=TradingConfig)
    fees: FeeConfig = Field(default_factory=FeeConfig)
    broker: BrokerConfig = Field(default_factory=BrokerConfig)

    @model_validator(mode="after")
    def _two_key_live_switch(self) -> "AppConfig":
        if self.trading.dry_run and self.broker.type != "paper":
            raise ValueError(
                "dry_run=true 时 broker.type 必须为 paper；"
                "切换实盘请同时设置 dry_run=false 和 broker.type=\"easytrader\""
            )
        if not self.trading.dry_run and self.broker.type != "easytrader":
            raise ValueError(
                "dry_run=false 时需要 broker.type=\"easytrader\"；"
                "模拟盘请保持 dry_run=true"
            )
        return self

    def validate_runtime(self) -> None:
        if len(self.server.api_key) < 16:
            raise ConfigError(
                "server.api_key 至少需要 16 个字符。请运行 "
                "`easytrader-service init-config` 生成配置，或用环境变量 ETS_API_KEY 覆盖。"
            )


def _resolve(base: Path, value: str) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return str(path.resolve())


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).expanduser().resolve()
    if not config_path.exists():
        raise ConfigError(f"配置文件不存在：{config_path}")
    try:
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"配置文件 TOML 解析失败：{exc}") from exc

    env_key = os.environ.get("ETS_API_KEY")
    if env_key:
        server = data.setdefault("server", {})
        if not isinstance(server, dict):
            raise ConfigError("配置中的 [server] 必须是表")
        server["api_key"] = env_key

    try:
        config = AppConfig.model_validate(data)
    except Exception as exc:  # pydantic ValidationError and field errors
        raise ConfigError(f"配置校验失败：{exc}") from exc

    base = config_path.parent
    config.database.path = _resolve(base, config.database.path)
    config.broker.paper.state_path = _resolve(base, config.broker.paper.state_path)
    return config


def config_template_text() -> str:
    """Return the commented example config shipped in the package."""
    try:
        from importlib.resources import files

        return (
            files("easytrader_service")
            .joinpath("templates/config.example.toml")
            .read_text(encoding="utf-8")
        )
    except Exception as exc:  # pragma: no cover - packaging fallback
        raise ConfigError(f"无法读取配置模板：{exc}") from exc
