"""Tiny HTTP helper with explicit timeouts and no third-party dependencies."""

from __future__ import annotations

import gzip
import urllib.error
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


class SourceError(RuntimeError):
    """A quote source failed for a specific request."""


def fetch_text(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 4.0,
    encoding: str = "utf-8",
) -> str:
    """Fetch ``url`` and decode it, raising :class:`SourceError` on failure."""

    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Connection": "close",
    }
    if headers:
        request_headers.update(headers)

    request = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            content_encoding = (response.headers.get("Content-Encoding") or "").lower()
    except urllib.error.HTTPError as exc:
        raise SourceError(f"HTTP {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise SourceError(f"网络错误: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SourceError("请求超时") from exc
    except OSError as exc:  # includes socket timeouts and connection resets
        raise SourceError(f"网络错误: {exc}") from exc

    if content_encoding == "gzip":
        try:
            raw = gzip.decompress(raw)
        except OSError as exc:
            raise SourceError(f"gzip 解压失败: {exc}") from exc

    try:
        return raw.decode(encoding, errors="replace")
    except LookupError as exc:
        raise SourceError(f"不支持的编码 {encoding}") from exc
