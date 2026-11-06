"""Human friendly CLI for the same quote stack used by the MCP server."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from stock_quote_mcp import __version__
from stock_quote_mcp.pool import AllSourcesFailed, SourcePool
from stock_quote_mcp.sources import default_sources
from stock_quote_mcp.tools import StockQuoteTools, ToolError


def _number(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    if isinstance(value, (int, float)):
        return f"{value:,.{digits}f}"
    return str(value)


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    line = "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    print(line)
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)))


def _quotes(tools: StockQuoteTools, symbols: list[str], as_json: bool) -> int:
    try:
        payload = tools.get_realtime_quotes({"symbols": symbols})
    except ToolError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    rows = []
    for quote in payload["quotes"]:
        rows.append(
            [
                quote["symbol"],
                quote["name"],
                _number(quote["price"]),
                _number(quote["change"]),
                _number(quote["change_pct"]) + "%",
                _number(quote["volume_lots"], 0),
                _number(None if quote["amount_yuan"] is None else quote["amount_yuan"] / 1e8),
                quote["timestamp"] or "-",
            ]
        )
    _print_table(
        ["代码", "名称", "最新价", "涨跌", "涨跌幅", "成交量(手)", "成交额(亿)", "行情时间"],
        rows,
    )
    for error in payload["errors"]:
        print(f"跳过 {error['symbol']}: {error['reason']}", file=sys.stderr)
    print(f"\n数据源: {payload['source']}  耗时: {payload['elapsed_ms']}ms")
    return 0


def _minutes(tools: StockQuoteTools, symbol: str, as_json: bool) -> int:
    try:
        payload = tools.get_intraday_minutes({"symbol": symbol})
    except ToolError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    bars = payload["bars"]
    print(
        f"{payload['symbol']} {payload['name']} {payload['date']} "
        f"共 {len(bars)} 根 1 分钟 K 线（数据源 {payload['source']}）"
    )
    rows = [
        [
            bar["time"],
            _number(bar["price"]),
            _number(bar["volume_lots"], 0),
            _number(bar["amount_yuan"] / 1e4),
        ]
        for bar in (bars[:3] + bars[-3:] if len(bars) > 6 else bars)
    ]
    _print_table(["时间", "价格", "成交量(手)", "成交额(万)"], rows)
    if len(bars) > 6:
        print("（仅显示首尾各 3 根，完整数据请加 --json）")
    return 0


def _market(tools: StockQuoteTools, as_json: bool) -> int:
    try:
        payload = tools.get_market_overview({})
    except ToolError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    session = payload["session"]
    print(f"{session['now']}  {session['label']} ({session['phase']})")
    rows = [
        [
            quote["name"],
            _number(quote["price"]),
            _number(quote["change"]),
            _number(quote["change_pct"]) + "%",
            quote["timestamp"] or "-",
        ]
        for quote in payload["indices"]
    ]
    _print_table(["指数", "点位", "涨跌", "涨跌幅", "行情时间"], rows)
    print(f"\n数据源: {payload['source']}")
    print(session["note"])
    return 0


def _search(tools: StockQuoteTools, keyword: str, as_json: bool) -> int:
    try:
        payload = tools.search_symbols({"keyword": keyword})
    except ToolError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    rows = [
        [item["symbol"], item["name"], item["type"], item["pinyin"]]
        for item in payload["results"]
    ]
    if not rows:
        print("没有找到匹配的证券。")
        return 0
    _print_table(["代码", "名称", "类型", "拼音"], rows)
    return 0


def _sources(tools: StockQuoteTools, as_json: bool) -> int:
    report = tools.check_data_sources({})
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    rows = []
    for source in report["sources"]:
        rows.append(
            [
                source["source"],
                "可用" if source["ok"] else "失败",
                _number(source.get("latency_ms"), 1),
                source.get("error") or "",
            ]
        )
    _print_table(["数据源", "状态", "延迟(ms)", "错误"], rows)
    return 0 if any(source["ok"] for source in report["sources"]) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stock-quote",
        description="A 股盘中实时行情命令行（与 MCP 服务共用数据源与容错逻辑）。",
    )
    parser.add_argument("--version", action="version", version=f"stock-quote {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    quotes = subparsers.add_parser("quotes", help="查询实时行情")
    quotes.add_argument("symbols", nargs="+", help="股票代码，例如 600519 000001 sh000001")
    quotes.add_argument("--json", action="store_true", help="输出完整 JSON")

    minutes = subparsers.add_parser("minutes", help="查询当日分时成交")
    minutes.add_argument("symbol", help="股票代码")
    minutes.add_argument("--json", action="store_true", help="输出完整 JSON")

    market = subparsers.add_parser("market", help="查询主要指数与交易时段")
    market.add_argument("--json", action="store_true", help="输出完整 JSON")

    search = subparsers.add_parser("search", help="搜索证券代码")
    search.add_argument("keyword", help="名称、拼音或代码")
    search.add_argument("--json", action="store_true", help="输出完整 JSON")

    sources = subparsers.add_parser("sources", help="检查数据源可用性")
    sources.add_argument("--json", action="store_true", help="输出完整 JSON")

    args = parser.parse_args(argv)
    tools = StockQuoteTools(SourcePool(default_sources()))

    try:
        if args.command == "quotes":
            return _quotes(tools, args.symbols, args.json)
        if args.command == "minutes":
            return _minutes(tools, args.symbol, args.json)
        if args.command == "market":
            return _market(tools, args.json)
        if args.command == "search":
            return _search(tools, args.keyword, args.json)
        if args.command == "sources":
            return _sources(tools, args.json)
    except AllSourcesFailed as exc:  # pragma: no cover - defensive
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
