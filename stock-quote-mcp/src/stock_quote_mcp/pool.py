"""Multi-source failover, short-lived caching and source health tracking."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Any, Callable

from stock_quote_mcp.http import SourceError
from stock_quote_mcp.models import QuoteBatch
from stock_quote_mcp.symbols import Symbol

CACHE_TTLS = {
    "quotes": 2.0,
    "intraday": 10.0,
    "search": 300.0,
}

CST = timezone(timedelta(hours=8))


@dataclass
class SourceState:
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None
    last_latency_ms: float | None = None
    cooldown_until: float = 0.0

    def to_dict(self, now: float) -> dict[str, Any]:
        return {
            "successes": self.successes,
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "last_latency_ms": None
            if self.last_latency_ms is None
            else round(self.last_latency_ms, 1),
            "cooling_down": now < self.cooldown_until,
            "cooldown_remaining_s": max(0.0, round(self.cooldown_until - now, 1)),
        }


@dataclass
class PoolResult:
    value: Any
    source: str
    elapsed_ms: float
    from_cache: bool = False


@dataclass
class AllSourcesFailed(SourceError):
    operation: str
    errors: dict[str, str] = field(default_factory=dict)

    def __str__(self) -> str:
        details = "; ".join(f"{name}: {reason}" for name, reason in self.errors.items())
        return f"所有数据源均失败 ({self.operation})" + (f": {details}" if details else "")


class SourcePool:
    """Try sources in order, remember failures and briefly cache results.

    A source that fails twice in a row is skipped for ``cooldown_seconds`` so a
    broken endpoint cannot add its timeout to every single request.  A later
    successful call resets the failure counter.
    """

    def __init__(
        self,
        sources: list[Any],
        *,
        clock: Callable[[], float] = time.monotonic,
        cooldown_seconds: float = 30.0,
        failure_threshold: int = 2,
        cache_ttls: dict[str, float] | None = None,
    ) -> None:
        if not sources:
            raise ValueError("至少需要一个数据源")
        self.sources = list(sources)
        self.clock = clock
        self.cooldown_seconds = cooldown_seconds
        self.failure_threshold = failure_threshold
        self.cache_ttls = dict(CACHE_TTLS)
        if cache_ttls:
            self.cache_ttls.update(cache_ttls)
        self.states: dict[str, SourceState] = {source.name: SourceState() for source in sources}
        self._cache: dict[tuple[Any, ...], tuple[float, PoolResult]] = {}
        self._lock = threading.Lock()

    # -- public operations -------------------------------------------------

    def quotes(self, symbols: list[Symbol]) -> PoolResult:
        key = ("quotes", tuple(symbol.canonical for symbol in symbols))
        return self._call(
            "quotes",
            key,
            lambda source: source.fetch_quotes(symbols),
        )

    def intraday(self, symbol: Symbol) -> PoolResult:
        key = ("intraday", symbol.canonical)
        return self._call(
            "intraday",
            key,
            lambda source: source.fetch_intraday(symbol),
        )

    def search(self, keyword: str) -> PoolResult:
        key = ("search", keyword.strip())
        return self._call(
            "search",
            key,
            lambda source: source.search(keyword.strip()),
        )

    def check_sources(self) -> dict[str, Any]:
        """Probe every source directly, bypassing cache and cooldown."""

        symbols = [Symbol("SH", "600519"), Symbol("SH", "000300")]
        now = self.clock()
        checks = []
        for source in self.sources:
            started = time.monotonic()
            entry: dict[str, Any] = {
                "source": source.name,
                "supports": sorted(getattr(source, "supports", set())),
            }
            if "quotes" not in getattr(source, "supports", set()):
                entry.update(ok=False, error="数据源未实现实时行情")
                checks.append(entry)
                continue
            try:
                batch: QuoteBatch = source.fetch_quotes(symbols)
                entry.update(
                    ok=True,
                    latency_ms=round((time.monotonic() - started) * 1000, 1),
                    sample=[
                        {
                            "symbol": quote.symbol,
                            "name": quote.name,
                            "price": quote.price,
                            "timestamp": quote.timestamp,
                        }
                        for quote in batch.quotes
                    ],
                    errors=batch.errors,
                )
            except SourceError as exc:
                entry.update(
                    ok=False,
                    latency_ms=round((time.monotonic() - started) * 1000, 1),
                    error=str(exc),
                )
            except Exception as exc:  # defensive: a source must not crash the tool
                entry.update(
                    ok=False,
                    latency_ms=round((time.monotonic() - started) * 1000, 1),
                    error=f"{type(exc).__name__}: {exc}",
                )
            checks.append(entry)

        return {
            "checked_at": datetime.now(CST).isoformat(timespec="seconds"),
            "sources": checks,
            "state": {
                source.name: self.states[source.name].to_dict(now) for source in self.sources
            },
        }

    # -- internals ---------------------------------------------------------

    def _call(self, operation: str, key: tuple[Any, ...], call: Callable[[Any], Any]) -> PoolResult:
        ttl = self.cache_ttls.get(operation, 0.0)
        now = self.clock()

        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > now:
                return PoolResult(
                    value=cached[1].value,
                    source=cached[1].source,
                    elapsed_ms=cached[1].elapsed_ms,
                    from_cache=True,
                )

        errors: dict[str, str] = {}
        skipped: list[str] = []
        for source in self.sources:
            supports = getattr(source, "supports", set())
            if operation not in supports:
                errors[source.name] = "不支持该操作"
                continue

            state = self.states[source.name]
            now = self.clock()
            if now < state.cooldown_until:
                skipped.append(source.name)
                continue

            started = time.monotonic()
            try:
                value = call(source)
            except SourceError as exc:
                self._record_failure(source.name, state, str(exc))
                errors[source.name] = str(exc)
                continue
            except Exception as exc:  # defensive: keep trying other sources
                self._record_failure(source.name, state, f"{type(exc).__name__}: {exc}")
                errors[source.name] = f"{type(exc).__name__}: {exc}"
                continue

            latency = (time.monotonic() - started) * 1000
            self._record_success(state, latency)
            result = PoolResult(value=value, source=source.name, elapsed_ms=round(latency, 1))
            if ttl > 0:
                with self._lock:
                    self._cache[key] = (self.clock() + ttl, result)
            return result

        if skipped:
            errors["skipped"] = f"冷却中的数据源: {', '.join(skipped)}"
        raise AllSourcesFailed(operation=operation, errors=errors)

    def _record_failure(self, name: str, state: SourceState, reason: str) -> None:
        state.failures += 1
        state.consecutive_failures += 1
        state.last_error = reason
        if state.consecutive_failures >= self.failure_threshold:
            state.cooldown_until = self.clock() + self.cooldown_seconds

    @staticmethod
    def _record_success(state: SourceState, latency_ms: float) -> None:
        state.successes += 1
        state.consecutive_failures = 0
        state.last_latency_ms = latency_ms
        state.last_error = None

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()
