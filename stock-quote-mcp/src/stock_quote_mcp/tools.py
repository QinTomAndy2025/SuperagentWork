"""MCP tool implementations on top of :class:`SourcePool`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from stock_quote_mcp.pool import AllSourcesFailed, PoolResult, SourcePool
from stock_quote_mcp.symbols import SymbolError, normalize_symbol

CST = timezone(timedelta(hours=8))

MAX_SYMBOLS = 50

INDEX_ALIASES = ["上证指数", "深证成指", "创业板指", "科创50", "沪深300", "北证50"]


class ToolError(RuntimeError):
    """A tool call failed in a way the caller can fix or report."""


def _now_iso() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def _session_info() -> dict[str, Any]:
    now = datetime.now(CST)
    minutes = now.hour * 60 + now.minute
    weekday = now.weekday()

    if weekday >= 5:
        phase, label = "weekend", "周末休市"
    elif minutes < 9 * 60 + 15:
        phase, label = "pre_open", "开盘前"
    elif minutes <= 9 * 60 + 25:
        phase, label = "call_auction", "集合竞价"
    elif minutes < 9 * 60 + 30:
        phase, label = "pre_open_gap", "开盘前静默期"
    elif minutes < 11 * 60 + 30:
        phase, label = "trading_am", "上午连续交易"
    elif minutes < 13 * 60:
        phase, label = "lunch_break", "午间休市"
    elif minutes < 15 * 60:
        phase, label = "trading_pm", "下午连续交易"
    else:
        phase, label = "closed", "收盘"

    return {
        "now": _now_iso(),
        "phase": phase,
        "label": label,
        "is_weekday": weekday < 5,
        "note": "交易时段按北京时间与工作日推断，不识别法定节假日；请以行情时间戳为准。",
    }


def _failure_message(exc: AllSourcesFailed) -> str:
    details = "；".join(f"{name}: {reason}" for name, reason in exc.errors.items())
    return f"实时行情获取失败（{exc.operation}）。数据源尝试结果：{details}"


class StockQuoteTools:
    def __init__(self, pool: SourcePool) -> None:
        self.pool = pool
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "get_realtime_quotes": self.get_realtime_quotes,
            "get_intraday_minutes": self.get_intraday_minutes,
            "get_market_overview": self.get_market_overview,
            "search_symbols": self.search_symbols,
            "check_data_sources": self.check_data_sources,
        }

    def call(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        handler = self._handlers.get(name)
        if handler is None:
            raise ToolError(f"未知工具: {name}")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise ToolError("arguments 必须是 JSON 对象")
        return handler(arguments)

    # -- tools ------------------------------------------------------------

    def get_realtime_quotes(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raw_symbols = arguments.get("symbols")
        if not isinstance(raw_symbols, list):
            raise ToolError("symbols 必须是字符串数组，例如 [\"600519\", \"000001\"]")
        if not raw_symbols:
            raise ToolError("symbols 不能为空")
        if len(raw_symbols) > MAX_SYMBOLS:
            raise ToolError(f"一次最多查询 {MAX_SYMBOLS} 个代码")

        symbols = []
        errors: list[dict[str, str]] = []
        seen: set[str] = set()
        for raw in raw_symbols:
            try:
                symbol = normalize_symbol(raw)
            except SymbolError as exc:
                errors.append({"symbol": str(raw), "reason": str(exc)})
                continue
            if symbol.canonical in seen:
                continue
            seen.add(symbol.canonical)
            symbols.append(symbol)

        if not symbols:
            raise ToolError("没有可查询的代码: " + "；".join(item["reason"] for item in errors))

        try:
            result = self.pool.quotes(symbols)
        except AllSourcesFailed as exc:
            raise ToolError(_failure_message(exc)) from exc

        batch = result.value
        errors.extend(batch.errors)
        return {
            "as_of": _now_iso(),
            "source": result.source,
            "elapsed_ms": result.elapsed_ms,
            "from_cache": result.from_cache,
            "count": len(batch.quotes),
            "quotes": [quote.to_dict() for quote in batch.quotes],
            "errors": errors,
        }

    def get_intraday_minutes(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raw = arguments.get("symbol")
        if not isinstance(raw, str) or not raw.strip():
            raise ToolError("symbol 必须是股票代码，例如 \"600519\"")
        try:
            symbol = normalize_symbol(raw)
        except SymbolError as exc:
            raise ToolError(str(exc)) from exc

        try:
            result = self.pool.intraday(symbol)
        except AllSourcesFailed as exc:
            raise ToolError(_failure_message(exc)) from exc

        payload = result.value.to_dict()
        if not payload["bars"]:
            raise ToolError(f"{symbol.canonical} 当前没有分时数据（可能停牌或尚未开盘）")
        payload.update(
            {
                "as_of": _now_iso(),
                "source": result.source,
                "elapsed_ms": result.elapsed_ms,
                "from_cache": result.from_cache,
            }
        )
        return payload

    def get_market_overview(self, arguments: dict[str, Any]) -> dict[str, Any]:
        symbols = [normalize_symbol(alias) for alias in INDEX_ALIASES]
        try:
            result: PoolResult = self.pool.quotes(symbols)
        except AllSourcesFailed as exc:
            raise ToolError(_failure_message(exc)) from exc

        batch = result.value
        return {
            "as_of": _now_iso(),
            "source": result.source,
            "elapsed_ms": result.elapsed_ms,
            "from_cache": result.from_cache,
            "session": _session_info(),
            "indices": [quote.to_dict() for quote in batch.quotes],
            "errors": batch.errors,
        }

    def search_symbols(self, arguments: dict[str, Any]) -> dict[str, Any]:
        keyword = arguments.get("keyword")
        if not isinstance(keyword, str) or not keyword.strip():
            raise ToolError("keyword 不能为空，例如 \"茅台\" 或 \"600519\"")
        keyword = keyword.strip()
        if len(keyword) > 30:
            raise ToolError("keyword 最长 30 个字符")

        try:
            result = self.pool.search(keyword)
        except AllSourcesFailed as exc:
            raise ToolError(_failure_message(exc)) from exc

        return {
            "keyword": keyword,
            "source": result.source,
            "elapsed_ms": result.elapsed_ms,
            "from_cache": result.from_cache,
            "count": len(result.value),
            "results": result.value,
        }

    def check_data_sources(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.pool.check_sources()


def _schema(
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "get_realtime_quotes",
        "description": (
            "查询 A 股、ETF、指数的实时行情快照：最新价、涨跌幅、开高低、成交量/额、"
            "五档盘口、涨跌停价与行情时间戳。支持批量（最多 50 个），"
            "自动在腾讯/新浪/东方财富之间故障切换。"
        ),
        "inputSchema": _schema(
            {
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": MAX_SYMBOLS,
                    "description": (
                        "股票代码，支持 600519、sh600519、600519.SH、000001.SZ；"
                        "上证指数请写 sh000001 或 上证指数。"
                    ),
                }
            },
            ["symbols"],
        ),
    },
    {
        "name": "get_intraday_minutes",
        "description": (
            "查询单只 A 股、ETF 或指数当日的 1 分钟分时成交：每分钟价格、"
            "成交量（手）、成交额（元）。"
        ),
        "inputSchema": _schema(
            {
                "symbol": {
                    "type": "string",
                    "description": "证券代码，例如 600519、sh600519、600519.SH。",
                }
            },
            ["symbol"],
        ),
    },
    {
        "name": "get_market_overview",
        "description": (
            "查询上证指数、深证成指、创业板指、科创50、沪深300、北证50 的实时点位，"
            "并返回当前处于集合竞价/连续交易/午休/收盘等哪个时段。"
        ),
        "inputSchema": _schema({}),
    },
    {
        "name": "search_symbols",
        "description": "按名称、拼音或代码搜索 A 股、ETF、指数，返回标准代码（如 600519.SH）。",
        "inputSchema": _schema(
            {
                "keyword": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 30,
                    "description": "搜索关键词，例如 茅台、gzmt、600519。",
                }
            },
            ["keyword"],
        ),
    },
    {
        "name": "check_data_sources",
        "description": (
            "逐个探测腾讯、新浪、东方财富行情接口的可用性与延迟，"
            "用于排查行情取不到时的数据源问题。"
        ),
        "inputSchema": _schema({}),
    },
]
