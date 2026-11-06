from __future__ import annotations

import unittest

from stock_quote_mcp.http import SourceError
from stock_quote_mcp.models import IntradayResult, MinuteBar, Quote, QuoteBatch
from stock_quote_mcp.pool import AllSourcesFailed, SourcePool
from stock_quote_mcp.symbols import normalize_symbol


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakeSource:
    def __init__(
        self,
        name: str,
        *,
        fail: bool = False,
        supports: frozenset[str] = frozenset({"quotes", "intraday", "search"}),
    ) -> None:
        self.name = name
        self.fail = fail
        self.supports = supports
        self.calls = 0

    def fetch_quotes(self, symbols):
        self.calls += 1
        if self.fail:
            raise SourceError(f"{self.name} down")
        return QuoteBatch(
            quotes=[
                Quote(
                    symbol=symbol.canonical,
                    code=symbol.code,
                    market=symbol.market,
                    name=f"{self.name}-{symbol.code}",
                    price=1.0,
                    source=self.name,
                )
                for symbol in symbols
            ]
        )

    def fetch_intraday(self, symbol):
        self.calls += 1
        if self.fail:
            raise SourceError(f"{self.name} down")
        return IntradayResult(
            symbol=symbol.canonical,
            name="fake",
            date="2026-11-06",
            bars=[MinuteBar(time="09:30", price=1.0, volume_lots=10, amount_yuan=1000)],
            source=self.name,
        )

    def search(self, keyword):
        self.calls += 1
        if self.fail:
            raise SourceError(f"{self.name} down")
        return [{"symbol": "600519.SH", "name": keyword}]


class SourcePoolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()

    def pool(self, *sources, **kwargs) -> SourcePool:
        return SourcePool(list(sources), clock=self.clock, **kwargs)

    def test_failover_uses_next_source(self) -> None:
        bad = FakeSource("bad", fail=True)
        good = FakeSource("good")
        pool = self.pool(bad, good)
        result = pool.quotes([normalize_symbol("600519")])
        self.assertEqual(result.source, "good")
        self.assertEqual(result.value.quotes[0].name, "good-600519")
        self.assertEqual(pool.states["bad"].failures, 1)
        self.assertEqual(pool.states["good"].successes, 1)

    def test_failing_source_cools_down_after_threshold(self) -> None:
        bad = FakeSource("bad", fail=True)
        good = FakeSource("good")
        pool = self.pool(bad, good, failure_threshold=2, cooldown_seconds=30)

        pool.quotes([normalize_symbol("600519")])
        self.clock.now = 1
        pool.quotes([normalize_symbol("000001")])
        self.assertTrue(pool.states["bad"].to_dict(self.clock.now)["cooling_down"])

        self.clock.now = 2
        pool.quotes([normalize_symbol("000300")])
        self.assertEqual(bad.calls, 2, "cooling source must be skipped")

        self.clock.now = 31
        pool.quotes([normalize_symbol("000002")])
        self.assertEqual(bad.calls, 3, "source is retried after cooldown")

    def test_cache_avoids_duplicate_requests(self) -> None:
        good = FakeSource("good")
        pool = self.pool(good)
        symbols = [normalize_symbol("600519")]
        first = pool.quotes(symbols)
        second = pool.quotes(symbols)
        self.assertFalse(first.from_cache)
        self.assertTrue(second.from_cache)
        self.assertEqual(good.calls, 1)

        self.clock.now = 3
        third = pool.quotes(symbols)
        self.assertFalse(third.from_cache)
        self.assertEqual(good.calls, 2)

    def test_cooldown_does_not_count_unsupported_sources(self) -> None:
        search_only = FakeSource("search-only", supports=frozenset({"search"}))
        good = FakeSource("good")
        pool = self.pool(search_only, good)
        result = pool.quotes([normalize_symbol("600519")])
        self.assertEqual(result.source, "good")
        self.assertEqual(search_only.calls, 0)
        self.assertEqual(pool.states["search-only"].failures, 0)

    def test_all_sources_fail_raises_with_details(self) -> None:
        pool = self.pool(
            FakeSource("one", fail=True),
            FakeSource("two", fail=True),
        )
        with self.assertRaises(AllSourcesFailed) as context:
            pool.quotes([normalize_symbol("600519")])
        self.assertIn("one", context.exception.errors)
        self.assertIn("two", context.exception.errors)
        self.assertEqual(context.exception.operation, "quotes")

    def test_success_resets_consecutive_failures(self) -> None:
        flaky = FakeSource("flaky", fail=True)
        pool = self.pool(flaky)
        symbols = [normalize_symbol("600519")]

        with self.assertRaises(AllSourcesFailed):
            pool.quotes(symbols)
        flaky.fail = False
        pool.clear_cache()
        pool.quotes(symbols)
        self.assertEqual(pool.states["flaky"].consecutive_failures, 0)

        flaky.fail = True
        pool.clear_cache()
        with self.assertRaises(AllSourcesFailed):
            pool.quotes(symbols)
        self.assertEqual(pool.states["flaky"].consecutive_failures, 1)
        self.assertFalse(pool.states["flaky"].to_dict(self.clock.now)["cooling_down"])

    def test_intraday_and_search_failover(self) -> None:
        bad = FakeSource("bad", fail=True)
        good = FakeSource("good")
        pool = self.pool(bad, good)
        self.assertEqual(pool.intraday(normalize_symbol("600519")).source, "good")
        self.assertEqual(pool.search("茅台").value[0]["symbol"], "600519.SH")

    def test_check_sources_probes_without_cache(self) -> None:
        good = FakeSource("good")
        pool = self.pool(good)
        report = pool.check_sources()
        self.assertTrue(report["sources"][0]["ok"])
        self.assertFalse(pool.states["good"].to_dict(self.clock.now)["cooling_down"])


if __name__ == "__main__":
    unittest.main()
