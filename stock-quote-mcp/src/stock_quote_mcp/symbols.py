"""Symbol normalisation for A-share stocks, funds and indices.

Accepted forms::

    600519              -> 600519.SH
    sh600519 / SH600519 -> 600519.SH
    600519.SH           -> 600519.SH
    000001              -> 000001.SZ   (Ping An Bank; use sh000001 for the index)
    899050              -> 899050.BJ
    上证指数             -> 000001.SH

The canonical output uses the national format ``<code>.<MARKET>``; the
``prefix`` form is what the quote endpoints expect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PREFIX_RE = re.compile(r"^(SH|SZ|BJ)(\d{6})$")
_SUFFIX_RE = re.compile(r"^(\d{6})\.(SH|SZ|BJ)$")
_PLAIN_RE = re.compile(r"^\d{6}$")

# Plain six-digit codes that are indices on the Shanghai exchange.  ``000001``
# is deliberately absent: as a bare code it almost always means Ping An Bank
# (000001.SZ); the Shanghai Composite has to be written ``sh000001`` or by name.
_KNOWN_SH_INDICES = frozenset(
    {
        "000016",  # 上证50
        "000300",  # 沪深300
        "000688",  # 科创50
        "000852",  # 中证1000
        "000905",  # 中证500
    }
)

ALIASES = {
    "上证指数": "sh000001",
    "上证综指": "sh000001",
    "上证": "sh000001",
    "sh000001": "sh000001",
    "深证成指": "sz399001",
    "深证指数": "sz399001",
    "sz399001": "sz399001",
    "创业板指": "sz399006",
    "创业板": "sz399006",
    "sz399006": "sz399006",
    "科创50": "sh000688",
    "科创板50": "sh000688",
    "沪深300": "sh000300",
    "hs300": "sh000300",
    "中证500": "sh000905",
    "中证1000": "sh000852",
    "北证50": "bj899050",
    "北交所50": "bj899050",
}


class SymbolError(ValueError):
    """Raised when a symbol cannot be mapped to a supported A-share code."""


@dataclass(frozen=True)
class Symbol:
    """A normalised A-share security identifier."""

    market: str
    code: str

    @property
    def canonical(self) -> str:
        return f"{self.code}.{self.market}"

    @property
    def prefix(self) -> str:
        return f"{self.market.lower()}{self.code}"

    @property
    def is_index(self) -> bool:
        if self.market == "SH":
            return self.code.startswith("000")
        if self.market == "SZ":
            return self.code.startswith("399")
        if self.market == "BJ":
            return self.code.startswith("899")
        return False

    def __str__(self) -> str:
        return self.canonical


def _infer_market(code: str) -> str:
    if code in _KNOWN_SH_INDICES:
        return "SH"
    if code.startswith("399"):
        return "SZ"
    if code.startswith(("60", "68", "90", "5")):
        return "SH"
    if code.startswith(("00", "30", "20", "1")):
        return "SZ"
    if code.startswith(("43", "83", "87", "88", "92", "4", "8")):
        return "BJ"
    raise SymbolError(f"无法判断代码 {code} 的市场")


def normalize_symbol(raw: str) -> Symbol:
    """Return the canonical :class:`Symbol` for a user supplied identifier."""

    if not isinstance(raw, str):
        raise SymbolError("代码必须是字符串")
    text = raw.strip()
    if not text:
        raise SymbolError("代码不能为空")

    alias = ALIASES.get(text) or ALIASES.get(text.lower())
    if alias:
        text = alias

    upper = text.upper()
    match = _PREFIX_RE.match(upper)
    if match:
        market, code = match.group(1), match.group(2)
        return Symbol(market, code)

    match = _SUFFIX_RE.match(upper)
    if match:
        code, market = match.group(1), match.group(2)
        return Symbol(market, code)

    if _PLAIN_RE.match(text):
        return Symbol(_infer_market(text), text)

    raise SymbolError(f"无法识别的股票代码: {raw!r}（支持 600519、sh600519、600519.SH）")


def normalize_symbols(raw_symbols: object, *, limit: int = 50) -> list[Symbol]:
    """Normalise a list of symbols, preserving order and removing duplicates."""

    if not isinstance(raw_symbols, list):
        raise SymbolError("symbols 必须是字符串数组")
    if not raw_symbols:
        raise SymbolError("symbols 不能为空")
    if len(raw_symbols) > limit:
        raise SymbolError(f"一次最多查询 {limit} 个代码，收到 {len(raw_symbols)} 个")

    seen: set[str] = set()
    result: list[Symbol] = []
    for item in raw_symbols:
        symbol = normalize_symbol(item)
        if symbol.canonical in seen:
            continue
        seen.add(symbol.canonical)
        result.append(symbol)
    return result
