from __future__ import annotations

import io
import json
import unittest

from stock_quote_mcp.server import McpServer
from stock_quote_mcp.tools import ToolError


class FakeTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, name: str, arguments: dict | None) -> dict:
        arguments = arguments or {}
        self.calls.append((name, arguments))
        if name == "get_intraday_minutes":
            raise ToolError("没有分时数据")
        return {
            "echo": arguments,
            "source": "fake",
            "quotes": [],
            "errors": [],
            "count": 0,
        }


def request(method: str, request_id: int = 1, params: dict | None = None) -> dict:
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    return payload


class McpServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = FakeTools()
        self.server = McpServer(self.tools)

    def test_initialize_echoes_supported_protocol(self) -> None:
        response = self.server.handle(
            request(
                "initialize",
                params={
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            )
        )
        result = response["result"]
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertEqual(result["serverInfo"]["name"], "stock-quote-mcp")
        self.assertIn("tools", result["capabilities"])

    def test_initialize_falls_back_for_unknown_protocol(self) -> None:
        response = self.server.handle(
            request("initialize", params={"protocolVersion": "2026-07-28"})
        )
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")

    def test_tools_list_contains_five_tools(self) -> None:
        response = self.server.handle(request("tools/list"))
        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertEqual(
            names,
            [
                "get_realtime_quotes",
                "get_intraday_minutes",
                "get_market_overview",
                "search_symbols",
                "check_data_sources",
            ],
        )
        for tool in response["result"]["tools"]:
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_tools_call_returns_text_and_structured_content(self) -> None:
        self.server.handle(
            request(
                "initialize",
                params={"protocolVersion": "2025-06-18"},
            )
        )
        response = self.server.handle(
            request(
                "tools/call",
                params={
                    "name": "get_realtime_quotes",
                    "arguments": {"symbols": ["600519"]},
                },
            )
        )
        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertEqual(result["structuredContent"]["echo"], {"symbols": ["600519"]})
        self.assertEqual(json.loads(result["content"][0]["text"])["source"], "fake")

    def test_old_protocol_omits_structured_content(self) -> None:
        self.server.handle(
            request("initialize", params={"protocolVersion": "2024-11-05"})
        )
        response = self.server.handle(
            request(
                "tools/call",
                params={"name": "get_realtime_quotes", "arguments": {}},
            )
        )
        self.assertNotIn("structuredContent", response["result"])

    def test_tool_error_is_reported_as_tool_failure(self) -> None:
        response = self.server.handle(
            request(
                "tools/call",
                params={"name": "get_intraday_minutes", "arguments": {"symbol": "600519"}},
            )
        )
        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertIn("没有分时数据", result["content"][0]["text"])
        self.assertNotIn("error", response)

    def test_unknown_tool_and_method(self) -> None:
        unknown_tool = self.server.handle(
            request("tools/call", params={"name": "nope", "arguments": {}})
        )
        self.assertEqual(unknown_tool["error"]["code"], -32602)

        unknown_method = self.server.handle(request("resources/list"))
        self.assertEqual(unknown_method["error"]["code"], -32601)

    def test_notifications_get_no_response_and_ping_works(self) -> None:
        self.assertIsNone(self.server.handle(request("notifications/initialized")))
        self.assertEqual(self.server.handle(request("ping"))["result"], {})

    def test_serve_frames_responses_and_recovers_from_parse_error(self) -> None:
        stdin = io.StringIO(
            "not json\n"
            + json.dumps(request("ping", request_id=7))
            + "\n"
            + json.dumps(request("notifications/initialized"))
            + "\n"
        )
        stdout = io.StringIO()
        self.server.serve(stdin, stdout, io.StringIO())
        lines = [line for line in stdout.getvalue().splitlines() if line]
        self.assertEqual(len(lines), 2)

        parse_error = json.loads(lines[0])
        self.assertEqual(parse_error["error"]["code"], -32700)
        self.assertIsNone(parse_error["id"])

        pong = json.loads(lines[1])
        self.assertEqual(pong["id"], 7)
        self.assertEqual(pong["result"], {})

    def test_invalid_request_shape(self) -> None:
        response = self.server.handle({"id": 3, "result": 1})
        self.assertEqual(response["error"]["code"], -32600)


if __name__ == "__main__":
    unittest.main()
