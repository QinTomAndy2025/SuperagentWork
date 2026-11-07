from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from douyin_rpa.browser import open_browser  # noqa: E402
from douyin_rpa.errors import BrowserError  # noqa: E402

from mock_site import MockDouyinServer  # noqa: E402


@pytest.fixture
def mock_server():
    server = MockDouyinServer().start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def browser_context(tmp_path):
    manager = open_browser(profile_dir=tmp_path / "profile", headless=True, browser="chrome")
    try:
        context = manager.__enter__()
    except BrowserError as exc:  # 没有 Chrome/Chromium 时跳过浏览器测试
        pytest.skip(str(exc))
    try:
        yield context
    finally:
        manager.__exit__(None, None, None)


def login(context, server: MockDouyinServer) -> None:
    context.add_cookies([{"name": "mock_session", "value": "1", "url": server.url}])
