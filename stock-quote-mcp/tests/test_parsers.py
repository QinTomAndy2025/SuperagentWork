from __future__ import annotations

import json
import unittest
from pathlib import Path

from stock_quote_mcp.http import SourceError
from stock_quote_mcp.sources import (
    parse_eastmoney_quotes,
    parse_eastmoney_search,
    parse_eastmoney_trends,
    parse_sina_quotes,
    parse_tencent_intraday,
    parse_tencent_quotes,
    parse_tencent_search,
)
from stock_quote_mcp.symbols import normalize_symbol

FIXTURES = Path(__file__).parent / "fixtures"


def load_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def load_json(name: str) -> dict:
    return json.loads(load_text(name))


class TencentQuoteParserTests(unittest.TestCase):
    def test_parses_stock_index_and_bj_quotes(self) -> None:
        symbols = [
            normalize_symbol("600519"),
            normalize_symbol("000001"),
            normalize_symbol("sh000001"),
            normalize_symbol("bj899050"),
        ]
        batch = parse_tencent_quotes(load_text("tencent_quotes.txt"), symbols)
        self.assertEqual(len(batch.quotes), 4)
        self.assertEqual(batch.errors, [])

        maotai = batch.quotes[0]
        self.assertEqual(maotai.symbol, "600519.SH")
        self.assertEqual(maotai.name, "贵州茅台")
        self.assertAlmostEqual(maotai.price, 1453.00)
        self.assertAlmostEqual(maotai.prev_close, 1452.00)
        self.assertAlmostEqual(maotai.change, 1.00)
        self.assertAlmostEqual(maotai.change_pct, 0.07)
        self.assertEqual(maotai.volume_lots, 42848)
        self.assertAlmostEqual(maotai.amount_yuan, 6199528289.0, delta=20000)
        self.assertAlmostEqual(maotai.turnover_pct, 0.34)
        self.assertAlmostEqual(maotai.limit_up, 1597.20)
        self.assertAlmostEqual(maotai.limit_down, 1306.80)
        self.assertEqual(maotai.bids[0].price, 1452.0)
        self.assertEqual(maotai.bids[0].volume_lots, 2.0)
        self.assertEqual(maotai.asks[0].price, 1453.0)
        self.assertEqual(maotai.asks[0].volume_lots, 2.0)
        self.assertEqual(maotai.timestamp, "2026-11-06T16:14:58+08:00")
        self.assertEqual(maotai.source, "tencent")

        index = batch.quotes[2]
        self.assertTrue(index.symbol.endswith(".SH"))
        self.assertAlmostEqual(index.price, 3649.18)
        self.assertEqual(index.volume_lots, 477042561)
        self.assertGreater(index.amount_yuan or 0, 7.7e11)
        self.assertIsNone(index.limit_up)

        beijing = batch.quotes[3]
        self.assertEqual(beijing.market, "BJ")
        self.assertAlmostEqual(beijing.price, 888.48)
        self.assertAlmostEqual(beijing.change_pct, -2.37)

    def test_missing_symbol_is_reported_not_raised(self) -> None:
        symbols = [normalize_symbol("600519"), normalize_symbol("sh999999")]
        batch = parse_tencent_quotes(load_text("tencent_quotes.txt"), symbols)
        self.assertEqual(len(batch.quotes), 1)
        self.assertEqual(batch.errors[0]["symbol"], "999999.SH")

    def test_empty_payload_raises(self) -> None:
        with self.assertRaises(SourceError):
            parse_tencent_quotes(load_text("tencent_invalid.txt"), [normalize_symbol("sh999999")])

    def test_minute_payload(self) -> None:
        result = parse_tencent_intraday(load_json("tencent_minutes.json"), normalize_symbol("600519"))
        self.assertEqual(result.date, "2026-11-06")
        self.assertEqual(result.name, "贵州茅台")
        self.assertEqual(len(result.bars), 267)
        self.assertEqual(result.bars[0].time, "09:30")
        self.assertAlmostEqual(result.bars[0].price, 1450.00)
        self.assertEqual(result.bars[0].volume_lots, 1561)
        self.assertAlmostEqual(result.bars[0].amount_yuan, 226345000.00)
        self.assertEqual(result.bars[1].volume_lots, 2904)
        self.assertEqual(result.bars[-1].time, "15:30")

    def test_search_payload(self) -> None:
        results = parse_tencent_search(load_text("tencent_search.txt"))
        self.assertTrue(results)
        self.assertEqual(results[0]["symbol"], "000001.SZ")
        self.assertEqual(results[0]["name"], "平安银行")
        self.assertTrue(all(item["market"] in {"SH", "SZ", "BJ"} for item in results))


class SinaQuoteParserTests(unittest.TestCase):
    def test_parses_stock_and_index(self) -> None:
        symbols = [
            normalize_symbol("600519"),
            normalize_symbol("sz000001"),
            normalize_symbol("sh000001"),
            normalize_symbol("sh000300"),
        ]
        batch = parse_sina_quotes(load_text("sina_quotes.txt"), symbols)
        self.assertEqual(len(batch.quotes), 4)

        maotai = batch.quotes[0]
        self.assertAlmostEqual(maotai.price, 1453.0)
        self.assertAlmostEqual(maotai.volume_lots, 42847.72)
        self.assertAlmostEqual(maotai.amount_yuan, 6199528289.0)
        self.assertEqual(maotai.bids[0].price, 1452.0)
        self.assertEqual(maotai.bids[0].volume_lots, 2.0)
        self.assertEqual(maotai.asks[0].price, 1453.0)
        self.assertEqual(maotai.timestamp, "2026-11-06T15:34:59+08:00")

        index = batch.quotes[2]
        self.assertEqual(index.symbol, "000001.SH")
        self.assertAlmostEqual(index.price, 3649.1774)
        self.assertEqual(index.volume_lots, 477042561)
        self.assertGreater(index.amount_yuan or 0, 7.7e11)


class EastmoneyParserTests(unittest.TestCase):
    def test_parses_stock_index_and_etf(self) -> None:
        symbols = [
            normalize_symbol("600519"),
            normalize_symbol("000001"),
            normalize_symbol("sh000300"),
            normalize_symbol("bj899050"),
            normalize_symbol("510300"),
        ]
        batch = parse_eastmoney_quotes(load_json("eastmoney_quotes.json"), symbols)
        self.assertEqual(len(batch.quotes), 5)
        self.assertEqual(batch.errors, [])

        maotai = batch.quotes[0]
        self.assertAlmostEqual(maotai.price, 1453.00)
        self.assertAlmostEqual(maotai.change, 1.00)
        self.assertAlmostEqual(maotai.change_pct, 0.07)
        self.assertEqual(maotai.volume_lots, 42848)
        self.assertAlmostEqual(maotai.turnover_pct, 0.34)
        self.assertAlmostEqual(maotai.open, 1450.00)
        self.assertAlmostEqual(maotai.high, 1457.72)
        self.assertAlmostEqual(maotai.low, 1436.00)

        etf = batch.quotes[4]
        self.assertAlmostEqual(etf.price, 4.124)
        self.assertAlmostEqual(etf.change, -0.071)
        self.assertAlmostEqual(etf.change_pct, -1.69)

        beijing = batch.quotes[3]
        self.assertEqual(beijing.market, "BJ")
        self.assertAlmostEqual(beijing.price, 888.48)

    def test_missing_symbol_is_reported(self) -> None:
        batch = parse_eastmoney_quotes(
            load_json("eastmoney_quotes.json"), [normalize_symbol("000002")]
        )
        self.assertEqual(batch.quotes, [])
        self.assertEqual(batch.errors[0]["symbol"], "000002.SZ")

    def test_trends_payload(self) -> None:
        result = parse_eastmoney_trends(
            load_json("eastmoney_trends.json"), normalize_symbol("600519")
        )
        self.assertEqual(result.name, "贵州茅台")
        self.assertEqual(result.date, "2026-11-06")
        self.assertEqual(len(result.bars), 241)
        self.assertEqual(result.bars[0].time, "09:30")
        self.assertAlmostEqual(result.bars[0].price, 1450.00)
        self.assertEqual(result.bars[0].volume_lots, 1561)
        self.assertAlmostEqual(result.bars[0].avg_price, 1450.0)
        self.assertEqual(result.bars[-1].time, "15:00")

    def test_trends_empty_payload_raises(self) -> None:
        with self.assertRaises(SourceError):
            parse_eastmoney_trends({}, normalize_symbol("600519"))

    def test_search_payload(self) -> None:
        results = parse_eastmoney_search(load_json("eastmoney_search.json"))
        self.assertTrue(results)
        self.assertEqual(results[0]["symbol"], "600519.SH")
        self.assertEqual(results[0]["name"], "贵州茅台")


if __name__ == "__main__":
    unittest.main()
