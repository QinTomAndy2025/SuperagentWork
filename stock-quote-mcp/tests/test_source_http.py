from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from stock_quote_mcp.http import SourceError
from stock_quote_mcp.sources import (
    EastmoneySource,
    SinaSource,
    TencentSource,
)
from stock_quote_mcp.symbols import normalize_symbol

FIXTURES = Path(__file__).parent / "fixtures"


def text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TencentSourceTests(unittest.TestCase):
    @patch("stock_quote_mcp.sources.fetch_text")
    def test_quotes_request_url_and_headers(self, fetch_text) -> None:
        fetch_text.return_value = text("tencent_quotes.txt")
        batch = TencentSource().fetch_quotes([normalize_symbol("600519")])
        self.assertEqual(batch.quotes[0].name, "贵州茅台")

        call_args, call_kwargs = fetch_text.call_args
        self.assertIn("q=sh600519", call_args[0])
        self.assertEqual(call_kwargs["encoding"], "gbk")
        self.assertIn("Referer", call_kwargs["headers"])

    @patch("stock_quote_mcp.sources.fetch_text")
    def test_intraday_request(self, fetch_text) -> None:
        fetch_text.return_value = text("tencent_minutes.json")
        result = TencentSource().fetch_intraday(normalize_symbol("600519"))
        self.assertEqual(len(result.bars), 267)
        self.assertIn("code=sh600519", fetch_text.call_args[0][0])

    @patch("stock_quote_mcp.sources.fetch_text")
    def test_search_request(self, fetch_text) -> None:
        fetch_text.return_value = text("tencent_search.txt")
        results = TencentSource().search("平安")
        self.assertTrue(results)
        self.assertIn("q=%E5%B9%B3%E5%AE%89", fetch_text.call_args[0][0])


class SinaSourceTests(unittest.TestCase):
    @patch("stock_quote_mcp.sources.fetch_text")
    def test_requires_finance_referer(self, fetch_text) -> None:
        fetch_text.return_value = text("sina_quotes.txt")
        batch = SinaSource().fetch_quotes([normalize_symbol("600519")])
        self.assertEqual(batch.quotes[0].name, "贵州茅台")

        _args, call_kwargs = fetch_text.call_args
        self.assertEqual(call_kwargs["encoding"], "gbk")
        self.assertEqual(call_kwargs["headers"]["Referer"], "https://finance.sina.com.cn/")

    def test_intraday_and_search_are_unsupported(self) -> None:
        source = SinaSource()
        with self.assertRaises(SourceError):
            source.fetch_intraday(normalize_symbol("600519"))
        with self.assertRaises(SourceError):
            source.search("茅台")


class EastmoneySourceTests(unittest.TestCase):
    @patch("stock_quote_mcp.sources.fetch_text")
    def test_quotes_request(self, fetch_text) -> None:
        fetch_text.return_value = text("eastmoney_quotes.json")
        batch = EastmoneySource().fetch_quotes(
            [normalize_symbol("600519"), normalize_symbol("sh000300")]
        )
        self.assertEqual(len(batch.quotes), 2)
        self.assertIn("secids=1.600519,1.000300", fetch_text.call_args[0][0])

    @patch("stock_quote_mcp.sources.fetch_text")
    def test_intraday_request(self, fetch_text) -> None:
        fetch_text.return_value = text("eastmoney_trends.json")
        result = EastmoneySource().fetch_intraday(normalize_symbol("600519"))
        self.assertEqual(len(result.bars), 241)
        self.assertIn("secid=1.600519", fetch_text.call_args[0][0])

    @patch("stock_quote_mcp.sources.fetch_text")
    def test_search_request(self, fetch_text) -> None:
        fetch_text.return_value = text("eastmoney_search.json")
        results = EastmoneySource().search("茅台")
        self.assertEqual(results[0]["symbol"], "600519.SH")

    @patch("stock_quote_mcp.sources.fetch_text")
    def test_invalid_json_raises_source_error(self, fetch_text) -> None:
        fetch_text.return_value = "<html>blocked</html>"
        with self.assertRaises(SourceError):
            EastmoneySource().fetch_quotes([normalize_symbol("600519")])

    @patch("stock_quote_mcp.sources.fetch_text")
    def test_json_list_payload_rejected(self, fetch_text) -> None:
        fetch_text.return_value = json.dumps([1, 2, 3])
        with self.assertRaises(SourceError):
            EastmoneySource().search("茅台")


if __name__ == "__main__":
    unittest.main()
