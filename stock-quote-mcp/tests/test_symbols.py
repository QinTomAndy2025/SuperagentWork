from __future__ import annotations

import unittest

from stock_quote_mcp.symbols import Symbol, SymbolError, normalize_symbol, normalize_symbols


class NormalizeSymbolTests(unittest.TestCase):
    def test_plain_codes_infer_market(self) -> None:
        self.assertEqual(normalize_symbol("600519"), Symbol("SH", "600519"))
        self.assertEqual(normalize_symbol("000001"), Symbol("SZ", "000001"))
        self.assertEqual(normalize_symbol("399001"), Symbol("SZ", "399001"))
        self.assertEqual(normalize_symbol("899050"), Symbol("BJ", "899050"))
        self.assertEqual(normalize_symbol("430047"), Symbol("BJ", "430047"))
        self.assertEqual(normalize_symbol("920001"), Symbol("BJ", "920001"))
        self.assertEqual(normalize_symbol("510300"), Symbol("SH", "510300"))
        self.assertEqual(normalize_symbol("159915"), Symbol("SZ", "159915"))

    def test_known_shanghai_indices(self) -> None:
        self.assertEqual(normalize_symbol("000300"), Symbol("SH", "000300"))
        self.assertEqual(normalize_symbol("000688"), Symbol("SH", "000688"))

    def test_prefix_and_suffix_forms(self) -> None:
        self.assertEqual(normalize_symbol("sh600519"), Symbol("SH", "600519"))
        self.assertEqual(normalize_symbol("SH600519"), Symbol("SH", "600519"))
        self.assertEqual(normalize_symbol("600519.SH"), Symbol("SH", "600519"))
        self.assertEqual(normalize_symbol(" 600519.sh "), Symbol("SH", "600519"))

    def test_aliases(self) -> None:
        self.assertEqual(normalize_symbol("上证指数").prefix, "sh000001")
        self.assertEqual(normalize_symbol("创业板指").prefix, "sz399006")
        self.assertEqual(normalize_symbol("沪深300").prefix, "sh000300")
        self.assertEqual(normalize_symbol("北证50").prefix, "bj899050")

    def test_canonical_and_prefix(self) -> None:
        symbol = normalize_symbol("600519")
        self.assertEqual(symbol.canonical, "600519.SH")
        self.assertEqual(symbol.prefix, "sh600519")
        self.assertFalse(symbol.is_index)
        self.assertTrue(normalize_symbol("sh000001").is_index)
        self.assertTrue(normalize_symbol("sz399006").is_index)
        self.assertTrue(normalize_symbol("bj899050").is_index)

    def test_invalid(self) -> None:
        for value in ["", "  ", "ABC", "60051", "6005191", "sh6005199", "600519.HK", 600519]:
            with self.subTest(value=value):
                with self.assertRaises(SymbolError):
                    normalize_symbol(value)  # type: ignore[arg-type]

    def test_normalize_symbols_deduplicates_and_checks_limit(self) -> None:
        symbols = normalize_symbols(["600519", "sh600519", "000001"])
        self.assertEqual([symbol.canonical for symbol in symbols], ["600519.SH", "000001.SZ"])

        with self.assertRaises(SymbolError):
            normalize_symbols(["600519"] * 51)

        with self.assertRaises(SymbolError):
            normalize_symbols("600519")


if __name__ == "__main__":
    unittest.main()
