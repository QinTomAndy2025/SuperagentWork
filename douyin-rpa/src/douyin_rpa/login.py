"""扫码登录：二维码由用户本人用抖音 App 扫描。

脚本不接触手机号、密码或短信验证码。二维码截图会保存到磁盘，方便在
无头/远程机器上把图片转给用户。
"""

from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Page

from . import browser, config, selectors as sel
from .errors import LoginTimeout

LOGIN_HINT = (
    "请用抖音 App 扫描二维码（约 2 分钟有效，过期会自动刷新）。\n"
    "如果脚本跑在远程/无头机器上，把保存下来的 PNG 发到手机，"
    "或在抖音「扫一扫」里从相册选择这张图。"
)


@dataclass
class LoginResult:
    already_logged_in: bool
    qr_path: Path | None
    page_url: str


def is_logged_in(page: Page) -> bool:
    """判断当前页面是否处于已登录状态。

    只看 URL 不够：未登录时访问上传页，抖音会保留 /creator-micro/... 的 URL
    并在页面里渲染登录弹层。这里要求看到登录后才有的界面元素：
    上传页的文件控件，或创作者中心导航。
    """

    url = (page.url or "").lower()
    if "/creator-micro" not in url or "login" in url:
        return False
    try:
        if sel.has_visible_spec(page, config.DEFAULT_SELECTORS["login_marker"], timeout=0):
            return False
    except Exception:
        return False
    try:
        if page.locator("input[type=file]").count() > 0:
            return True
    except Exception:
        pass
    try:
        return sel.has_visible_spec(page, config.DEFAULT_SELECTORS["logged_in_hint"], timeout=0)
    except Exception:
        return False


def _verify_by_upload_page(page: Page, *, base_url: str | None = None) -> bool:
    """在扫码后的新页面里打开上传页，用文件控件确认登录真的生效。

    只做一次短检查：这个函数在主等待循环里调用，不能长时间阻塞二维码刷新。
    """

    probe = None
    try:
        probe = page.context.new_page()
        browser.goto(probe, config.upload_url(base_url), timeout_ms=10_000)
        probe.wait_for_timeout(1200)
        return is_logged_in(probe)
    except Exception:
        return False
    finally:
        if probe is not None:
            try:
                probe.close()
            except Exception:
                pass


def _find_qr_src(page: Page) -> str | None:
    """在登录页找到二维码图片的 data URL。"""

    images = page.locator("img")
    try:
        count = min(images.count(), 80)
    except Exception:
        return None
    for index in range(count):
        image = images.nth(index)
        try:
            if not image.is_visible():
                continue
            src = image.get_attribute("src") or ""
            if not src.startswith("data:image/png;base64,"):
                continue
            box = image.bounding_box()
            if not box:
                continue
            width, height = box["width"], box["height"]
            if 100 <= width <= 400 and abs(width - height) <= 8:
                return src
        except Exception:
            continue
    return None


def _write_qr(src: str, path: Path) -> str:
    raw = src.split(",", 1)[1]
    data = base64.b64decode(raw)
    digest = hashlib.sha256(data).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    # 内容没变就不重写，让文件 mtime 能代表“最后一次真正刷新”。
    if path.exists():
        try:
            if path.read_bytes() == data:
                return digest
        except Exception:
            pass
    path.write_bytes(data)
    return digest


def capture_login_qr(page: Page, qr_path: str | Path) -> tuple[Path, str] | None:
    """保存当前登录页二维码；找不到时返回 None。"""

    path = Path(qr_path).expanduser()
    src = _find_qr_src(page)
    if src is None:
        # 密码登录/验证码登录页需要先点到扫码登录。
        tab = sel.find_visible(page, page, config.DEFAULT_SELECTORS["login_qr_tab"], timeout=0)
        if tab is not None:
            sel.click(tab)
            page.wait_for_timeout(1500)
            src = _find_qr_src(page)
    if src is None:
        return None
    return path, _write_qr(src, path)


def wait_for_login(
    page: Page,
    *,
    qr_path: Path,
    timeout_s: float = 300,
    poll_ms: int = 1500,
    base_url: str | None = None,
) -> LoginResult:
    """等待用户扫码；期间二维码变化/失效时自动刷新同一张 PNG。"""

    deadline = time.monotonic() + max(1.0, timeout_s)
    captured = None
    for _ in range(20):
        captured = capture_login_qr(page, qr_path)
        if captured is not None:
            break
        page.wait_for_timeout(500)
    if captured is None:
        raise LoginTimeout("登录页上没有找到二维码，请确认网络和抖音登录页是否正常")
    last_hash = captured[1]
    last_verify = 0.0
    last_refresh = time.monotonic()
    print(f"登录二维码已保存：{qr_path}")
    print(LOGIN_HINT)

    def refresh() -> None:
        nonlocal captured, last_hash, last_refresh
        browser.goto(page, page.url or config.default_base_url())
        page.wait_for_timeout(3000)
        captured = capture_login_qr(page, qr_path)
        if captured is not None:
            last_hash = captured[1]
            last_refresh = time.monotonic()
            print(f"二维码已刷新：{qr_path}")

    while time.monotonic() < deadline:
        page.wait_for_timeout(poll_ms)
        if is_logged_in(page):
            return LoginResult(already_logged_in=False, qr_path=qr_path, page_url=page.url)
        if "/creator-micro" in (page.url or "").lower() and time.monotonic() - last_verify > 20:
            last_verify = time.monotonic()
            if _verify_by_upload_page(page, base_url=base_url):
                return LoginResult(already_logged_in=False, qr_path=qr_path, page_url=page.url)

        try:
            expired = sel.has_visible_spec(page, config.DEFAULT_SELECTORS["login_expired"], timeout=0)
        except Exception:
            expired = False
        if expired:
            refresh()
            continue

        captured = capture_login_qr(page, qr_path)
        if captured is not None and captured[1] != last_hash:
            last_hash = captured[1]
            last_refresh = time.monotonic()
            print(f"二维码已刷新：{qr_path}")

        # 即使页面没有显示“已失效”，也定期重新加载，保证用户拿到的二维码不会太旧。
        if time.monotonic() - last_refresh > 90:
            refresh()

    raise LoginTimeout(
        f"等待扫码登录超时（{int(timeout_s)} 秒）。请重新运行 `douyin-rpa login`，"
        "二维码有效期只有约 2 分钟。"
    )


def login_flow(
    *,
    profile_dir: str | Path,
    headless: bool = True,
    browser_name: str = "chrome",
    base_url: str | None = None,
    qr_path: str | Path,
    timeout_s: float = 300,
) -> LoginResult:
    """打开登录页 → 保存二维码 → 等待扫码。"""

    qr = Path(qr_path).expanduser()
    with browser.open_browser(
        profile_dir=profile_dir,
        headless=headless,
        browser=browser_name,
        base_url=base_url,
    ) as context:
        page = browser.first_page(context)
        browser.goto(page, config.upload_url(base_url))
        page.wait_for_timeout(2500)

        # 已登录时上传页会直接出现文件控件；未登录时同一 URL 会显示登录浮层。
        for _ in range(6):
            if is_logged_in(page):
                return LoginResult(already_logged_in=True, qr_path=None, page_url=page.url)
            page.wait_for_timeout(500)

        result = wait_for_login(page, qr_path=qr, timeout_s=timeout_s, base_url=base_url)
        browser.save_storage_state(context, profile_dir)
        try:
            page.screenshot(path=str(qr.parent / "login-success.png"))
        except Exception:
            pass
        return result


def status_flow(
    *,
    profile_dir: str | Path,
    headless: bool = True,
    browser_name: str = "chrome",
    base_url: str | None = None,
    timeout_s: float = 20,
) -> bool:
    """打开上传页判断当前 profile 是否仍处于登录状态。"""

    with browser.open_browser(
        profile_dir=profile_dir,
        headless=headless,
        browser=browser_name,
        base_url=base_url,
    ) as context:
        page = browser.first_page(context)
        browser.goto(page, config.upload_url(base_url))
        deadline = time.monotonic() + max(1.0, timeout_s)
        while time.monotonic() < deadline:
            if is_logged_in(page):
                browser.save_storage_state(context, profile_dir)
                return True
            try:
                if sel.has_visible_spec(page, config.DEFAULT_SELECTORS["login_marker"], timeout=0):
                    return False
            except Exception:
                pass
            page.wait_for_timeout(500)
        return is_logged_in(page)
