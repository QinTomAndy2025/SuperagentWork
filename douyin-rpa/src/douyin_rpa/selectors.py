"""在页面里按候选列表查找元素。

抖音的类名是构建产物生成的（例如 ``zone-container`` 之外还可能变化），
所以这里优先用可见文本、placeholder、无障碍角色，CSS 只作为补充。
"""

from __future__ import annotations

import json
import time
from typing import Iterable

from playwright.sync_api import Locator, Page

LocatorLike = Page | Locator


def build_locator(root: LocatorLike, spec: str) -> Locator:
    """把带前缀的选择器描述变成 Playwright Locator。"""

    if spec.startswith("role:"):
        parts = spec.split(":", 2)
        if len(parts) != 3 or not parts[1] or not parts[2]:
            raise ValueError(f"role 选择器格式应为 role:<role>:<name>：{spec!r}")
        return root.get_by_role(parts[1], name=parts[2], exact=True)  # type: ignore[arg-type]
    if spec.startswith("text!:"):
        return root.get_by_text(spec[6:], exact=True)
    if spec.startswith("text:"):
        return root.get_by_text(spec[5:], exact=False)
    if spec.startswith("placeholder:"):
        value = spec[len("placeholder:") :]
        quoted = json.dumps(value, ensure_ascii=False)
        return root.locator(f"input[placeholder*={quoted}], textarea[placeholder*={quoted}]")
    if spec.startswith("css:"):
        return root.locator(spec[4:])
    return root.locator(spec)


def find_visible(
    root: LocatorLike,
    page: Page,
    specs: Iterable[str],
    *,
    timeout: float = 0,
    require_enabled: bool = False,
    poll_interval_ms: int = 250,
) -> Locator | None:
    """返回第一个可见（可选：可用）的元素，找不到返回 None。"""

    deadline = time.monotonic() + max(0.0, timeout)
    specs = list(specs)
    while True:
        for spec in specs:
            try:
                locator = build_locator(root, spec)
                count = min(locator.count(), 20)
            except Exception:
                continue
            for index in range(count):
                candidate = locator.nth(index)
                try:
                    if not candidate.is_visible():
                        continue
                    if require_enabled and not candidate.is_enabled():
                        continue
                    return candidate
                except Exception:
                    continue
        if time.monotonic() >= deadline:
            return None
        page.wait_for_timeout(poll_interval_ms)


def has_visible_text(page: Page, texts: Iterable[str], *, timeout: float = 0) -> bool:
    """页面上是否出现了任意一段可见文本（传入纯文本）。"""

    return find_visible(page, page, [f"text:{text}" for text in texts], timeout=timeout) is not None


def has_visible_spec(page: Page, specs: Iterable[str], *, timeout: float = 0) -> bool:
    """页面上是否出现了任意一个选择器命中的可见元素（传入带前缀的选择器）。"""

    return find_visible(page, page, specs, timeout=timeout) is not None


def wait_visible_text(page: Page, texts: Iterable[str], *, timeout: float = 5) -> bool:
    return has_visible_text(page, texts, timeout=timeout)


def click(candidate: Locator) -> None:
    """滚动到元素再点击，必要时强制点击（浮层偶尔会挡住按钮）。"""

    try:
        candidate.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    try:
        candidate.click(timeout=5000)
    except Exception:
        candidate.click(timeout=5000, force=True)


def fill_editor(page: Page, editor: Locator, text: str) -> None:
    """向 contenteditable 编辑器输入文本。

    用键盘输入而不是直接改 DOM，保证 React 等框架能收到 input 事件。
    """

    editor.scroll_into_view_if_needed(timeout=3000)
    editor.click()
    try:
        editor.fill("")
    except Exception:
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
    if text:
        page.keyboard.insert_text(text)
    page.wait_for_timeout(250)
    # 话题联想浮层可能留在页面上，按 Esc 收起。
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
