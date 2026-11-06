"""Free A-share quote sources and their parsers.

Three independent public endpoints are supported:

* Tencent (``qt.gtimg.cn`` + ``web.ifzq.gtimg.cn``): rich snapshot, minute bars.
* Sina (``hq.sinajs.cn``): snapshot, requires a finance.sina.com.cn Referer.
* Eastmoney (``push2.eastmoney.com`` + ``push2his.eastmoney.com``): JSON
  snapshot, minute trends and symbol search.

All parsers are pure functions over decoded response text so they can be tested
against captured fixtures without touching the network.
"""

from __future__ import annotations

import json
import re
from urllib.parse import quote as urlquote

from stock_quote_mcp.http import SourceError, fetch_text
from stock_quote_mcp.models import BookLevel, IntradayResult, MinuteBar, Quote, QuoteBatch
from stock_quote_mcp.symbols import Symbol

TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q={codes}"
TENCENT_MINUTE_URL = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}"
TENCENT_SEARCH_URL = "https://smartbox.gtimg.cn/s3/?q={keyword}&t=all"

SINA_QUOTE_URL = "https://hq.sinajs.cn/list={codes}"

EASTMONEY_ULIST_URL = (
    "https://push2.eastmoney.com/api/qt/ulist.np/get"
    "?secids={secids}&fields=f2,f3,f4,f5,f6,f8,f12,f13,f14,f15,f16,f17,f18"
)
EASTMONEY_TRENDS_URL = (
    "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
    "?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
    "&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1"
)
EASTMONEY_SEARCH_URL = (
    "https://searchapi.eastmoney.com/api/suggest/get"
    "?input={keyword}&type=14&token=D43BF722C8E33BDC906FB84D85E326E8&count=20"
)

TENCENT_HEADERS = {"Referer": "https://gu.qq.com/"}
SINA_HEADERS = {"Referer": "https://finance.sina.com.cn/"}
EASTMONEY_HEADERS = {"Referer": "https://quote.eastmoney.com/"}

_TENCENT_QUOTE_RE = re.compile(r'v_([a-z]{2})(\d{6})="([^"]*)"')
_SINA_QUOTE_RE = re.compile(r'var hq_str_([a-z]{2})(\d{6})="([^"]*)"')
_UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")


def _float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text in ("", "-", "--", "None"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _price_text(value: float | None) -> float | None:
    return None if value is None else float(value)


def _is_fund(symbol: Symbol) -> bool:
    """Funds/ETFs quote with three decimals on Eastmoney, stocks with two."""

    if symbol.market == "SH":
        return symbol.code.startswith("5")
    if symbol.market == "SZ":
        return symbol.code.startswith(("15", "16", "18"))
    return False


def _decimals(symbol: Symbol) -> int:
    return 3 if _is_fund(symbol) else 2


def _eastmoney_secid(symbol: Symbol) -> str:
    market = "1" if symbol.market == "SH" else "0"
    return f"{market}.{symbol.code}"


def _eastmoney_market(f13: object, code: str) -> str:
    if str(f13) == "1":
        return "SH"
    if code.startswith(("43", "83", "87", "88", "92", "4", "8")):
        return "BJ"
    return "SZ"


def _iso_time(raw: str | None) -> str | None:
    if not raw:
        return None
    match = re.match(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$", raw)
    if not match:
        return None
    year, month, day, hour, minute, second = match.groups()
    return f"{year}-{month}-{day}T{hour}:{minute}:{second}+08:00"


def _unescape_unicode(text: str) -> str:
    return _UNICODE_ESCAPE_RE.sub(lambda match: chr(int(match.group(1), 16)), text)


def _load_json(text: str) -> dict:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SourceError(f"返回不是合法 JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise SourceError("返回 JSON 不是对象")
    return payload


def _levels(pairs: list[tuple[float | None, float | None]]) -> list[BookLevel]:
    levels = []
    for price, volume in pairs:
        if price is not None and price > 0:
            levels.append(BookLevel(price=price, volume_lots=volume or 0.0))
    return levels


# ---------------------------------------------------------------------------
# Tencent
# ---------------------------------------------------------------------------


def parse_tencent_quotes(text: str, symbols: list[Symbol]) -> QuoteBatch:
    """Parse a ``qt.gtimg.cn`` GBK payload (already decoded)."""

    found: dict[str, list[str]] = {}
    for match in _TENCENT_QUOTE_RE.finditer(text):
        market, code, body = match.group(1), match.group(2), match.group(3)
        found[f"{market}{code}"] = body.split("~")

    if not found:
        raise SourceError("腾讯行情接口返回为空或格式异常")

    batch = QuoteBatch()
    for symbol in symbols:
        fields = found.get(symbol.prefix)
        if not fields or len(fields) < 38:
            batch.errors.append({"symbol": symbol.canonical, "reason": "代码不存在或无行情"})
            continue

        price = _float(fields[3])
        prev_close = _float(fields[4])
        change = _float(fields[31])
        change_pct = _float(fields[32])
        if change is None and price is not None and prev_close:
            change = round(price - prev_close, 4)
        if change_pct is None and change is not None and prev_close:
            change_pct = round(change / prev_close * 100, 4)

        limit_up = _float(fields[47])
        limit_down = _float(fields[48])
        amount_wan = _float(fields[37])

        bids = _levels(
            [
                (_float(fields[9 + index * 2]), _float(fields[10 + index * 2]))
                for index in range(5)
            ]
        )
        asks = _levels(
            [
                (_float(fields[19 + index * 2]), _float(fields[20 + index * 2]))
                for index in range(5)
            ]
        )

        batch.quotes.append(
            Quote(
                symbol=symbol.canonical,
                code=symbol.code,
                market=symbol.market,
                name=fields[1],
                price=_price_text(price),
                prev_close=_price_text(prev_close),
                open=_price_text(_float(fields[5])),
                high=_price_text(_float(fields[33])),
                low=_price_text(_float(fields[34])),
                change=_price_text(change),
                change_pct=_price_text(change_pct),
                volume_lots=_float(fields[36]),
                amount_yuan=None if amount_wan is None else amount_wan * 10000,
                turnover_pct=_float(fields[38]),
                limit_up=limit_up if limit_up and limit_up > 0 else None,
                limit_down=limit_down if limit_down and limit_down > 0 else None,
                bids=bids,
                asks=asks,
                timestamp=_iso_time(fields[30]),
                source="tencent",
            )
        )
    return batch


def parse_tencent_intraday(payload: dict, symbol: Symbol) -> IntradayResult:
    node = (payload.get("data") or {}).get(symbol.prefix) or {}
    data = node.get("data") or {}
    rows = data.get("data") or []
    if not rows:
        raise SourceError("腾讯分时接口无当日数据")

    name = ""
    qt = (node.get("qt") or {}).get(symbol.prefix) or []
    if isinstance(qt, list) and len(qt) > 1:
        name = qt[1]

    date_raw = str(data.get("date") or "")
    date = (
        f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}"
        if re.match(r"^\d{8}$", date_raw)
        else None
    )

    bars: list[MinuteBar] = []
    previous_volume = 0.0
    previous_amount = 0.0
    for row in rows:
        parts = str(row).split()
        if len(parts) < 4:
            continue
        raw_time, raw_price, raw_volume, raw_amount = parts[:4]
        price = _float(raw_price)
        cumulative_volume = _float(raw_volume) or 0.0
        cumulative_amount = _float(raw_amount) or 0.0
        volume = max(0.0, cumulative_volume - previous_volume)
        amount = max(0.0, cumulative_amount - previous_amount)
        previous_volume = cumulative_volume
        previous_amount = cumulative_amount
        if price is None:
            continue
        bars.append(
            MinuteBar(
                time=f"{raw_time[:2]}:{raw_time[2:]}",
                price=price,
                volume_lots=volume,
                amount_yuan=amount,
            )
        )

    return IntradayResult(
        symbol=symbol.canonical,
        name=name,
        date=date,
        bars=bars,
        source="tencent",
    )


def parse_tencent_search(text: str) -> list[dict[str, str]]:
    match = re.search(r'v_hint="([^"]*)"', text)
    if not match:
        raise SourceError("腾讯代码搜索接口返回格式异常")
    body = match.group(1).strip()
    if not body:
        return []

    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for chunk in body.split("^"):
        parts = chunk.split("~")
        if len(parts) < 4:
            continue
        market, code = parts[0].lower(), parts[1]
        if market not in {"sh", "sz", "bj"} or not re.match(r"^\d{6}$", code):
            continue
        name = _unescape_unicode(parts[2])
        symbol = f"{code}.{market.upper()}"
        if symbol in seen:
            continue
        seen.add(symbol)
        results.append(
            {
                "symbol": symbol,
                "code": code,
                "market": market.upper(),
                "name": name,
                "pinyin": parts[3],
                "type": parts[4] if len(parts) > 4 else "",
            }
        )
    return results[:20]


# ---------------------------------------------------------------------------
# Sina
# ---------------------------------------------------------------------------


def parse_sina_quotes(text: str, symbols: list[Symbol]) -> QuoteBatch:
    found: dict[str, list[str]] = {}
    for match in _SINA_QUOTE_RE.finditer(text):
        market, code, body = match.group(1), match.group(2), match.group(3)
        if body:
            found[f"{market}{code}"] = body.split(",")

    if not found:
        raise SourceError("新浪行情接口返回为空或格式异常")

    batch = QuoteBatch()
    for symbol in symbols:
        fields = found.get(symbol.prefix)
        if not fields or len(fields) < 32:
            batch.errors.append({"symbol": symbol.canonical, "reason": "代码不存在或无行情"})
            continue

        price = _float(fields[3])
        prev_close = _float(fields[2])
        volume_raw = _float(fields[8])
        volume_lots = None
        if volume_raw is not None:
            # Sina reports stock volume in shares; index volume is already in lots.
            volume_lots = volume_raw if symbol.is_index else volume_raw / 100.0

        change = None
        change_pct = None
        if price is not None and prev_close:
            change = round(price - prev_close, 4)
            change_pct = round(change / prev_close * 100, 4)

        def lots(value: float | None) -> float | None:
            return None if value is None else value / 100.0

        bids = _levels(
            [
                (_float(fields[11 + index * 2]), lots(_float(fields[10 + index * 2])))
                for index in range(5)
            ]
        )
        asks = _levels(
            [
                (_float(fields[21 + index * 2]), lots(_float(fields[20 + index * 2])))
                for index in range(5)
            ]
        )

        timestamp = None
        if fields[30] and fields[31]:
            timestamp = f"{fields[30]}T{fields[31]}+08:00"

        batch.quotes.append(
            Quote(
                symbol=symbol.canonical,
                code=symbol.code,
                market=symbol.market,
                name=fields[0],
                price=price,
                prev_close=prev_close,
                open=_float(fields[1]),
                high=_float(fields[4]),
                low=_float(fields[5]),
                change=change,
                change_pct=change_pct,
                volume_lots=volume_lots,
                amount_yuan=_float(fields[9]),
                bids=bids,
                asks=asks,
                timestamp=timestamp,
                source="sina",
            )
        )
    return batch


# ---------------------------------------------------------------------------
# Eastmoney
# ---------------------------------------------------------------------------


def parse_eastmoney_quotes(payload: dict, symbols: list[Symbol]) -> QuoteBatch:
    data = payload.get("data")
    rows = (data or {}).get("diff") if isinstance(data, dict) else None
    if rows is None:
        rows = []
    if isinstance(rows, dict):
        rows = [rows]

    by_symbol: dict[str, dict] = {}
    for row in rows:
        code = str(row.get("f12") or "")
        market = _eastmoney_market(row.get("f13"), code)
        by_symbol[f"{code}.{market}"] = row

    batch = QuoteBatch()
    for symbol in symbols:
        row = by_symbol.get(symbol.canonical)
        if not row:
            batch.errors.append({"symbol": symbol.canonical, "reason": "代码不存在或无行情"})
            continue

        scale = 10 ** _decimals(symbol)
        price = _float(row.get("f2"))
        prev_close = _float(row.get("f18"))
        change = _float(row.get("f4"))
        change_pct = _float(row.get("f3"))
        batch.quotes.append(
            Quote(
                symbol=symbol.canonical,
                code=symbol.code,
                market=symbol.market,
                name=str(row.get("f14") or ""),
                price=None if price is None else price / scale,
                prev_close=None if prev_close is None else prev_close / scale,
                open=(
                    None
                    if (value := _float(row.get("f17"))) is None
                    else value / scale
                ),
                high=(
                    None
                    if (value := _float(row.get("f15"))) is None
                    else value / scale
                ),
                low=(
                    None
                    if (value := _float(row.get("f16"))) is None
                    else value / scale
                ),
                change=None if change is None else change / scale,
                change_pct=None if change_pct is None else change_pct / 100.0,
                volume_lots=_float(row.get("f5")),
                amount_yuan=_float(row.get("f6")),
                turnover_pct=(
                    None
                    if (turnover := _float(row.get("f8"))) is None
                    else turnover / 100.0
                ),
                source="eastmoney",
            )
        )
    return batch


def parse_eastmoney_trends(payload: dict, symbol: Symbol) -> IntradayResult:
    data = payload.get("data") or {}
    rows = data.get("trends") or []
    if not rows:
        raise SourceError("东方财富分时接口无当日数据")

    name = str(data.get("name") or "")
    date = None
    bars: list[MinuteBar] = []
    for row in rows:
        parts = str(row).split(",")
        if len(parts) < 8:
            continue
        stamp, _open, close, _high, _low, volume, amount, average = parts[:8]
        if date is None and " " in stamp:
            date = stamp.split(" ", 1)[0]
        if " " not in stamp:
            continue
        price = _float(close)
        if price is None:
            continue
        bars.append(
            MinuteBar(
                time=stamp.split(" ", 1)[1],
                price=price,
                volume_lots=_float(volume) or 0.0,
                amount_yuan=_float(amount) or 0.0,
                avg_price=_float(average),
            )
        )

    return IntradayResult(
        symbol=symbol.canonical,
        name=name,
        date=date,
        bars=bars,
        source="eastmoney",
    )


def parse_eastmoney_search(payload: dict) -> list[dict[str, str]]:
    table = payload.get("QuotationCodeTable") or {}
    rows = table.get("Data") or []
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        code = str(row.get("Code") or "")
        if not re.match(r"^\d{6}$", code):
            continue
        quote_id = str(row.get("QuoteID") or "")
        market = "SH" if quote_id.startswith("1.") else _eastmoney_market(0, code)
        symbol = f"{code}.{market}"
        if symbol in seen:
            continue
        seen.add(symbol)
        results.append(
            {
                "symbol": symbol,
                "code": code,
                "market": market,
                "name": str(row.get("Name") or ""),
                "pinyin": str(row.get("PinYin") or ""),
                "type": str(row.get("SecurityTypeName") or ""),
            }
        )
    return results[:20]


# ---------------------------------------------------------------------------
# Source classes
# ---------------------------------------------------------------------------


class QuoteSource:
    name = "base"
    supports: frozenset[str] = frozenset()

    def fetch_quotes(self, symbols: list[Symbol]) -> QuoteBatch:
        raise SourceError("该数据源不支持实时行情")

    def fetch_intraday(self, symbol: Symbol) -> IntradayResult:
        raise SourceError("该数据源不支持分时数据")

    def search(self, keyword: str) -> list[dict[str, str]]:
        raise SourceError("该数据源不支持代码搜索")


class TencentSource(QuoteSource):
    name = "tencent"
    supports = frozenset({"quotes", "intraday", "search"})

    def fetch_quotes(self, symbols: list[Symbol]) -> QuoteBatch:
        codes = ",".join(symbol.prefix for symbol in symbols)
        text = fetch_text(
            TENCENT_QUOTE_URL.format(codes=codes),
            headers=TENCENT_HEADERS,
            encoding="gbk",
        )
        return parse_tencent_quotes(text, symbols)

    def fetch_intraday(self, symbol: Symbol) -> IntradayResult:
        text = fetch_text(
            TENCENT_MINUTE_URL.format(code=symbol.prefix),
            headers=TENCENT_HEADERS,
            encoding="utf-8",
        )
        return parse_tencent_intraday(_load_json(text), symbol)

    def search(self, keyword: str) -> list[dict[str, str]]:
        text = fetch_text(
            TENCENT_SEARCH_URL.format(keyword=urlquote(keyword, safe="")),
            headers=TENCENT_HEADERS,
            encoding="gbk",
        )
        return parse_tencent_search(text)


class SinaSource(QuoteSource):
    name = "sina"
    supports = frozenset({"quotes"})

    def fetch_quotes(self, symbols: list[Symbol]) -> QuoteBatch:
        codes = ",".join(symbol.prefix for symbol in symbols)
        # Sina answers 403 without a finance.sina.com.cn Referer.
        text = fetch_text(
            SINA_QUOTE_URL.format(codes=codes),
            headers=SINA_HEADERS,
            encoding="gbk",
        )
        return parse_sina_quotes(text, symbols)


class EastmoneySource(QuoteSource):
    name = "eastmoney"
    supports = frozenset({"quotes", "intraday", "search"})

    def fetch_quotes(self, symbols: list[Symbol]) -> QuoteBatch:
        secids = ",".join(_eastmoney_secid(symbol) for symbol in symbols)
        text = fetch_text(
            EASTMONEY_ULIST_URL.format(secids=secids),
            headers=EASTMONEY_HEADERS,
            encoding="utf-8",
        )
        return parse_eastmoney_quotes(_load_json(text), symbols)

    def fetch_intraday(self, symbol: Symbol) -> IntradayResult:
        text = fetch_text(
            EASTMONEY_TRENDS_URL.format(secid=_eastmoney_secid(symbol)),
            headers=EASTMONEY_HEADERS,
            encoding="utf-8",
        )
        return parse_eastmoney_trends(_load_json(text), symbol)

    def search(self, keyword: str) -> list[dict[str, str]]:
        text = fetch_text(
            EASTMONEY_SEARCH_URL.format(keyword=urlquote(keyword, safe="")),
            headers=EASTMONEY_HEADERS,
            encoding="utf-8",
        )
        return parse_eastmoney_search(_load_json(text))


def default_sources() -> list[QuoteSource]:
    return [TencentSource(), SinaSource(), EastmoneySource()]
