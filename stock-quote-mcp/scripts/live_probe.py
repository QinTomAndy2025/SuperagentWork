#!/usr/bin/env python3
"""Live stability probe for the three public quote sources.

This script is *not* part of the MCP server and is never run by the test suite;
it performs real network requests so the reported numbers reflect the current
endpoint behaviour.  Example::

    python3 scripts/live_probe.py --rounds 10 --interval 0.5 \
        --json-out docs/stability-probe.json

The market may be closed when this runs; endpoints then return the last
session snapshot, which still exercises availability, latency and parsing.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stock_quote_mcp.http import SourceError  # noqa: E402
from stock_quote_mcp.models import QuoteBatch  # noqa: E402
from stock_quote_mcp.sources import (  # noqa: E402
    EastmoneySource,
    SinaSource,
    TencentSource,
    default_sources,
)
from stock_quote_mcp.symbols import normalize_symbol  # noqa: E402

CST = timezone(timedelta(hours=8))
SYMBOLS = ["600519", "000001", "sh000300", "bj899050", "510300"]


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return round(ordered[index], 1)


def probe_source(source, rounds: int, interval: float) -> dict[str, Any]:
    symbols = [normalize_symbol(item) for item in SYMBOLS]
    latencies: list[float] = []
    failures: list[str] = []
    latest: dict[str, dict[str, Any]] = {}

    for index in range(rounds):
        started = time.monotonic()
        try:
            batch: QuoteBatch = source.fetch_quotes(symbols)
        except SourceError as exc:
            failures.append(str(exc))
        else:
            latencies.append((time.monotonic() - started) * 1000)
            latest = {quote.symbol: quote.to_dict() for quote in batch.quotes}
        if index + 1 < rounds:
            time.sleep(interval)

    return {
        "source": source.name,
        "rounds": rounds,
        "success": rounds - len(failures),
        "success_rate": round((rounds - len(failures)) / rounds, 3),
        "latency_ms": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "max": round(max(latencies), 1) if latencies else None,
        },
        "errors": failures[:5],
        "sample": latest,
    }


def compare_prices(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_symbol: dict[str, dict[str, float]] = {}
    for result in results:
        for symbol, quote in result["sample"].items():
            price = quote.get("price")
            if price:
                by_symbol.setdefault(symbol, {})[result["source"]] = price

    comparison = {}
    for symbol, prices in by_symbol.items():
        values = list(prices.values())
        if not values:
            continue
        spread_bps = (max(values) - min(values)) / min(values) * 10000
        comparison[symbol] = {
            "prices": prices,
            "max_spread_bps": round(spread_bps, 2),
        }
    return comparison


def probe_intraday() -> dict[str, Any]:
    symbol = normalize_symbol("600519")
    output: dict[str, Any] = {}
    for source in (TencentSource(), EastmoneySource()):
        started = time.monotonic()
        try:
            result = source.fetch_intraday(symbol)
        except SourceError as exc:
            output[source.name] = {"ok": False, "error": str(exc)}
            continue
        bars = result.bars
        total_volume = sum(bar.volume_lots for bar in bars)
        output[source.name] = {
            "ok": True,
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "date": result.date,
            "bars": len(bars),
            "first": bars[0].to_dict() if bars else None,
            "last": bars[-1].to_dict() if bars else None,
            "total_volume_lots": round(total_volume, 2),
        }
    return output


def probe_search() -> dict[str, Any]:
    output: dict[str, Any] = {}
    for source in (TencentSource(), EastmoneySource()):
        started = time.monotonic()
        try:
            results = source.search("贵州茅台")
        except SourceError as exc:
            output[source.name] = {"ok": False, "error": str(exc)}
            continue
        output[source.name] = {
            "ok": True,
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "count": len(results),
            "first": results[0] if results else None,
        }
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe the three public A-share quote sources.")
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)

    started_at = datetime.now(CST)
    print(f"probing {args.rounds} rounds/source, interval {args.interval}s ...", file=sys.stderr)

    results = []
    for source in default_sources():
        print(f"- {source.name} ...", file=sys.stderr)
        results.append(probe_source(source, args.rounds, args.interval))

    report = {
        "generated_at": started_at.isoformat(timespec="seconds"),
        "symbols": SYMBOLS,
        "rounds": args.rounds,
        "interval_s": args.interval,
        "sources": results,
        "price_consistency": compare_prices(results),
        "intraday": probe_intraday(),
        "search": probe_search(),
    }

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
