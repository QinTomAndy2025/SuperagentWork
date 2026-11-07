"""启动/复用带登录状态的 Chrome。

使用 ``launch_persistent_context``：扫码后的 Cookie、localStorage 都写在
profile 目录里，下一次发布直接复用，不需要再登录。
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from playwright.sync_api import BrowserContext, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from . import config
from .errors import BrowserError


@contextmanager
def open_browser(
    *,
    profile_dir: str | Path,
    headless: bool = True,
    browser: str = "chrome",
    slow_mo: int = 0,
    base_url: str | None = None,
) -> Iterator[BrowserContext]:
    """打开持久化浏览器上下文；退出时关闭并落盘 Cookie。"""

    profile = Path(profile_dir).expanduser()
    profile.mkdir(parents=True, exist_ok=True)
    channel = None if browser == "chromium" else browser
    args = [
        "--disable-blink-features=AutomationControlled",
        "--lang=zh-CN",
    ]
    kwargs = dict(
        user_data_dir=str(profile),
        headless=headless,
        args=args,
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        viewport={"width": 1440, "height": 900},
        slow_mo=slow_mo,
    )

    with sync_playwright() as playwright:
        try:
            if channel:
                context = playwright.chromium.launch_persistent_context(channel=channel, **kwargs)
            else:
                context = playwright.chromium.launch_persistent_context(**kwargs)
        except PlaywrightError as exc:
            hint = (
                "未找到可用的 Chrome/Chromium。请安装 Google Chrome，"
                "或运行 `playwright install chromium` 后加 `--browser chromium`。"
            )
            raise BrowserError(f"{hint}\n原始错误：{exc}") from exc

        try:
            # 去掉最明显的自动化标记；不做验证码绕过，扫码/风控仍由用户本人完成。
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )
            restore_storage_state(context, profile)
            context.set_default_timeout(15_000)
            yield context
        finally:
            context.close()


def first_page(context: BrowserContext) -> Page:
    page = context.pages[0] if context.pages else context.new_page()
    page.set_default_timeout(15_000)
    return page


def goto(page: Page, url: str, *, timeout_ms: int = 60_000) -> None:
    """打开页面；超时也继续，SPA 往往已经渲染出关键内容。"""

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except PlaywrightError:
        page.wait_for_timeout(1000)


def upload_url(base_url: str | None = None) -> str:
    return config.upload_url(base_url)


def storage_state_path(profile_dir: str | Path) -> Path:
    return Path(profile_dir).expanduser() / "storage-state.json"


def save_storage_state(context: BrowserContext, profile_dir: str | Path) -> Path | None:
    """把 Cookie / localStorage 另存一份。

    Chrome 关闭时可能丢弃会话 Cookie；这份状态在下次启动时补回，
    保证扫码一次后可以继续复用登录。
    """

    path = storage_state_path(profile_dir)
    try:
        state = context.storage_state()
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.chmod(path, 0o600)
        return path
    except Exception:
        return None


def restore_storage_state(context: BrowserContext, profile_dir: str | Path) -> None:
    path = storage_state_path(profile_dir)
    if not path.exists():
        return
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    cookies = state.get("cookies") or []
    if cookies:
        try:
            existing = {(cookie.get("name"), cookie.get("domain")) for cookie in context.cookies()}
            missing = [
                cookie for cookie in cookies
                if (cookie.get("name"), cookie.get("domain")) not in existing
            ]
            if missing:
                context.add_cookies(missing)
        except Exception:
            pass
    origins = state.get("origins") or []
    local_storage = []
    for origin in origins:
        for item in origin.get("localStorage") or []:
            if isinstance(item, dict) and "name" in item and "value" in item:
                local_storage.append({"origin": origin.get("origin"), "name": item["name"], "value": item["value"]})
    if local_storage:
        payload = json.dumps(local_storage, ensure_ascii=False)
        # 只补齐缺失的 key，避免用旧状态覆盖页面刚写入的新值。
        context.add_init_script(
            f"""(() => {{
                const items = {payload};
                for (const item of items) {{
                    if (item.origin === location.origin && localStorage.getItem(item.name) === null) {{
                        localStorage.setItem(item.name, item.value);
                    }}
                }}
            }})();"""
        )
