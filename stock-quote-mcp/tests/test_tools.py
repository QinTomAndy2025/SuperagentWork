from __future__ import annotations

import unittest

from stock_quote_mcp.models import MinuteBar, IntradayResult, Quote, QuoteBatch
from stock_quote_mcp.pool import AllSourcesFailed, PoolResult
from stock_quote_mcp.symbols import Symbol
from stock_quote_mcp.tools import ToolError, StockQuoteTools


class FakePool:
    def __init__(self) -> None:
        self.quote_requests: list[list[Symbol]] = []
        self.intraday_requests: list[Symbol] = []
        self.search_requests: list[str] = []

    def quotes(self, symbols: list[Symbol]) -> PoolResult:
        self.quote_requests.append(symbols)
        batch = QuoteBatch(
            quotes=[
                Quote(
                    symbol=symbol.canonical,
                    code=symbol.code,
                    market=symbol.market,
                    name=f"name-{symbol.code}",
                    price=1.5,
                )
                for symbol in symbols
            ]
        )
        return PoolResult(batch, "fake", 3.2)

    def intraday(self, symbol: Symbol) -> PoolResult:
        self.intraday_requests.append(symbol)
        if symbol.code == "000002":
            raise AllSourcesFailed("intraday", {"fake": "boom"})
        result = IntradayResult(
            symbol=symbol.canonical,
            name="name",
            date="2026-11-06",
            bars=[MinuteBar("09:30", 1.5, 10, 1500)],
        )
        return PoolResult(result, "fake", 4.5)

    def search(self, keyword: str) -> PoolResult:
        self.search_requests.append(keyword)
        return PoolResult([], "fake", 1.0)

    def check_sources(self) -> dict:
        return {"sources": [], "state": {}}


class ToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pool = FakePool()
        self.tools = StockQuoteTools(self.pool)

    def test_realtime_quotes_deduplicates_and_reports_bad_symbols(self) -> None:
        payload = self.tools.get_realtime_quotes(
            {"symbols": ["600519", "sh600519", "not-a-code"]}
        )
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["quotes"][0]["symbol"], "600519.SH")
        self.assertEqual(payload["errors"][0]["symbol"], "not-a-code")
        self.assertEqual(len(self.pool.quote_requests[0]), 1)

    def test_realtime_quotes_requires_a_valid_symbol(self) -> None:
        with self.assertRaises(ToolError):
            self.tools.get_realtime_quotes({"symbols": ["bad"]})
        with self.assertRaises(ToolError):
            self.tools.get_realtime_quotes({"symbols": "600519"})
        with self.assertRaises(ToolError):
            self.tools.get_realtime_quotes({"symbols": ["600519"] * 51})

    def test_intraday_minutes(self) -> None:
        payload = self.tools.get_intraday_minutes({"symbol": "600519"})
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["bars"][0]["time"], "09:30")
        with self.assertRaises(ToolError):
            self.tools.get_intraday_minutes({})

    def test_market_overview_queries_six_indices(self) -> None:
        payload = self.tools.get_market_overview({})
        self.assertEqual(len(payload["indices"]), 6)
        self.assertIn("phase", payload["session"])
        self.assertIn("note", payload["session"])

    def test_search_symbols(self) -> None:
        payload = self.tools.search_symbols({"keyword": " 茅台 "})
        self.assertEqual(payload["keyword"], "茅台")
        self.assertEqual(payload["count"], 0)
        with self.assertRaises(ToolError):
            self.tools.search_symbols({"keyword": ""})

    def test_check_sources_passthrough(self) -> None:
        self.assertEqual(self.tools.check_data_sources({}), {"sources": [], "state": {}})

    def test_unknown_tool_raises(self) -> None:
        with self.assertRaises(ToolError):
            self.tools.call("nope", {})


if __name__ == "__main__":
    unittest.main()
