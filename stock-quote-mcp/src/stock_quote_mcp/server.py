"""Minimal MCP stdio server (JSON-RPC 2.0 over newline-delimited JSON).

The server intentionally implements the small part of the Model Context
Protocol that it advertises: ``initialize``, ``tools/list`` and ``tools/call``
plus ``ping``.  It has no dependency on the official SDK, so it runs with any
Python 3.10+ interpreter, including machines without pip access.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from typing import Any, TextIO

from stock_quote_mcp import __version__
from stock_quote_mcp.pool import SourcePool
from stock_quote_mcp.sources import default_sources
from stock_quote_mcp.tools import TOOL_DEFINITIONS, StockQuoteTools, ToolError

SUPPORTED_PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18")
DEFAULT_PROTOCOL = "2025-06-18"

SERVER_INSTRUCTIONS = (
    "A 股盘中实时行情工具。查价格用 get_realtime_quotes，查分时用 "
    "get_intraday_minutes，查代码用 search_symbols。数据来自公开免费接口，"
    "非交易所授权行情，仅供研究参考。"
)


class McpServer:
    def __init__(self, tools: StockQuoteTools, *, version: str = __version__) -> None:
        self.tools = tools
        self.version = version
        self.negotiated_protocol = DEFAULT_PROTOCOL
        self.debug = False
        self._stderr: TextIO = sys.stderr

    # -- protocol ---------------------------------------------------------

    def handle(self, request: Any) -> dict[str, Any] | None:
        if not isinstance(request, dict):
            return self._error(None, -32600, "Invalid Request")

        request_id = request.get("id")
        method = request.get("method")
        if not isinstance(method, str):
            return self._error(request_id, -32600, "Invalid Request")

        if method.startswith("notifications/"):
            return None
        if method == "initialize":
            return self._initialize(request_id, request.get("params"))
        if method == "ping":
            return self._result(request_id, {})
        if method == "tools/list":
            return self._result(request_id, {"tools": TOOL_DEFINITIONS})
        if method == "tools/call":
            return self._call_tool(request_id, request.get("params"))

        return self._error(request_id, -32601, f"Method not found: {method}")

    def _initialize(self, request_id: Any, params: Any) -> dict[str, Any]:
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        self.negotiated_protocol = (
            requested if requested in SUPPORTED_PROTOCOLS else DEFAULT_PROTOCOL
        )
        return self._result(
            request_id,
            {
                "protocolVersion": self.negotiated_protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "stock-quote-mcp", "version": self.version},
                "instructions": SERVER_INSTRUCTIONS,
            },
        )

    def _call_tool(self, request_id: Any, params: Any) -> dict[str, Any]:
        if not isinstance(params, dict):
            return self._error(request_id, -32602, "tools/call 缺少参数对象")
        name = params.get("name")
        if not isinstance(name, str):
            return self._error(request_id, -32602, "tools/call 缺少工具名")
        known = {tool["name"] for tool in TOOL_DEFINITIONS}
        if name not in known:
            return self._error(request_id, -32602, f"未知工具: {name}")

        arguments = params.get("arguments")
        try:
            value = self.tools.call(name, arguments)
        except ToolError as exc:
            return self._result(
                request_id,
                {"content": [{"type": "text", "text": str(exc)}], "isError": True},
            )
        except Exception as exc:  # defensive: report, do not kill the server
            if self.debug:
                traceback.print_exc(file=self._stderr)
            return self._result(
                request_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": f"工具执行异常: {type(exc).__name__}: {exc}",
                        }
                    ],
                    "isError": True,
                },
            )

        result: dict[str, Any] = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(value, ensure_ascii=False, indent=2),
                }
            ],
            "isError": False,
        }
        if self.negotiated_protocol >= "2025-06-18":
            result["structuredContent"] = value
        return self._result(request_id, result)

    @staticmethod
    def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }

    # -- transport --------------------------------------------------------

    def serve(self, stdin: TextIO, stdout: TextIO, stderr: TextIO | None = None) -> None:
        if stderr is not None:
            self._stderr = stderr
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                response = self._error(None, -32700, f"Parse error: {exc.msg}")
            else:
                try:
                    response = self.handle(request)
                except Exception as exc:  # pragma: no cover - defensive
                    if self.debug:
                        traceback.print_exc(file=self._stderr)
                    request_id = request.get("id") if isinstance(request, dict) else None
                    response = self._error(request_id, -32603, f"Internal error: {exc}")
            if response is None:
                continue
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stock-quote-mcp",
        description="A-share intraday real-time quote MCP server (stdio transport).",
    )
    parser.add_argument("--version", action="version", version=f"stock-quote-mcp {__version__}")
    parser.add_argument(
        "--check",
        action="store_true",
        help="probe each quote source once and exit (does not start MCP)",
    )
    parser.add_argument("--debug", action="store_true", help="print unexpected tracebacks to stderr")
    args = parser.parse_args(argv)

    pool = SourcePool(default_sources())
    tools = StockQuoteTools(pool)

    if args.check:
        report = tools.check_data_sources({})
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if any(source["ok"] for source in report["sources"]) else 1

    server = McpServer(tools)
    server.debug = args.debug
    server.serve(sys.stdin, sys.stdout, sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
