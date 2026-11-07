"""在已登录的浏览器里上传并发布视频/图文。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from playwright.sync_api import BrowserContext, Locator, Page

from . import browser, config
from .content import IMAGES_KIND, Content
from .errors import DouyinRpaError, NotLoggedInError, PublishError
from .login import is_logged_in
from . import selectors as sel


@dataclass
class PublishResult:
    status: str  # "preview" 或 "published"
    artifacts_dir: Path
    final_url: str
    screenshots: list[Path] = field(default_factory=list)
    content: dict = field(default_factory=dict)


def new_artifacts_dir(home: str | Path | None = None, *, prefix: str = "publish") -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = Path(home).expanduser() if home else Path.cwd()
    path = base / "artifacts" / f"{stamp}-{prefix}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def publish_content(
    context: BrowserContext,
    content: Content,
    *,
    artifacts_dir: str | Path,
    base_url: str | None = None,
    dry_run: bool = True,
    keep_open: bool = False,
    selector_map: dict[str, list[str]] | None = None,
    timeout_s: float = 600,
) -> PublishResult:
    """执行一次发布。

    ``dry_run=True`` 时只上传媒体、填好标题和描述，不点击发布按钮。
    """

    selectors = selector_map or config.DEFAULT_SELECTORS
    art = Path(artifacts_dir).expanduser()
    art.mkdir(parents=True, exist_ok=True)
    page = context.new_page()
    page.set_default_timeout(15_000)
    screenshots: list[Path] = []
    try:
        browser.goto(page, config.upload_url(base_url))
        _wait_upload_ready(page, selectors, timeout_s=45)

        if content.kind == IMAGES_KIND:
            _open_image_tab(page, selectors)

        _upload_files(page, content)
        _wait_for_editor(page, selectors, timeout_s=90)
        _fill_fields(page, content, selectors)

        preview = _screenshot(page, art, "01-filled.png")
        if preview:
            screenshots.append(preview)

        publish_button = _wait_publishable(page, selectors, timeout_s=timeout_s)

        if dry_run:
            return PublishResult(
                status="preview",
                artifacts_dir=art,
                final_url=page.url,
                screenshots=screenshots,
                content=content.to_dict(),
            )

        # _click_publish 只在看到成功提示或跳到作品管理页后返回。
        _click_publish(page, publish_button, selectors)

        published = _screenshot(page, art, "02-published.png")
        if published:
            screenshots.append(published)
        return PublishResult(
            status="published",
            artifacts_dir=art,
            final_url=page.url,
            screenshots=screenshots,
            content=content.to_dict(),
        )
    except DouyinRpaError as exc:
        _record_failure(page, art)
        if isinstance(exc, PublishError) and exc.artifacts_dir is None:
            exc.artifacts_dir = art
        raise
    except Exception as exc:  # noqa: BLE001 - 转成带截图目录的可读错误
        _record_failure(page, art)
        raise PublishError(f"发布流程失败：{exc}", art) from exc
    finally:
        if not keep_open:
            try:
                page.close()
            except Exception:
                pass


def _wait_upload_ready(page: Page, selectors: dict[str, list[str]], *, timeout_s: float) -> None:
    deadline = time.monotonic() + max(5.0, timeout_s)
    while time.monotonic() < deadline:
        if is_logged_in(page):
            if _file_input_count(page) > 0:
                return
        else:
            try:
                if sel.has_visible_spec(page, selectors["login_marker"], timeout=0):
                    raise NotLoggedInError(
                        "当前浏览器 profile 未登录抖音。请先运行 `douyin-rpa login` 扫码登录，"
                        "再运行发布命令。"
                    )
            except NotLoggedInError:
                raise
            except Exception:
                pass
        page.wait_for_timeout(500)

    if not is_logged_in(page):
        raise NotLoggedInError(
            "当前浏览器 profile 未登录抖音。请先运行 `douyin-rpa login` 扫码登录。"
        )
    raise PublishError("上传页加载超时，页面上没有出现文件上传控件")


def _open_image_tab(page: Page, selectors: dict[str, list[str]]) -> None:
    tab = sel.find_visible(page, page, selectors["image_tab"], timeout=8)
    if tab is None:
        return
    sel.click(tab)
    page.wait_for_timeout(1200)


def _file_input_count(page: Page) -> int:
    try:
        return page.locator("input[type=file]").count()
    except Exception:
        return 0


def _find_file_input(page: Page, content: Content) -> Locator:
    deadline = time.monotonic() + 8
    while True:
        inputs = page.locator("input[type=file]")
        try:
            count = inputs.count()
        except Exception:
            count = 0
        wanted = "video" if content.kind != IMAGES_KIND else "image"
        fallback: Locator | None = None
        for index in range(count):
            candidate = inputs.nth(index)
            if fallback is None:
                fallback = candidate
            try:
                accept = (candidate.get_attribute("accept") or "").lower()
            except Exception:
                continue
            if wanted in accept:
                return candidate
        if fallback is not None:
            return fallback
        if time.monotonic() > deadline:
            raise PublishError("页面上没有找到上传文件的 input")
        page.wait_for_timeout(500)


def _upload_files(page: Page, content: Content) -> None:
    file_input = _find_file_input(page, content)
    files = [str(path.resolve()) for path in content.media]
    try:
        file_input.set_input_files(files)
    except Exception as exc:
        raise PublishError(f"设置上传文件失败：{exc}") from exc


def _wait_for_editor(page: Page, selectors: dict[str, list[str]], *, timeout_s: float) -> None:
    editor = sel.find_visible(
        page,
        page,
        selectors["caption_editor"] + selectors["title_input"],
        timeout=timeout_s,
    )
    if editor is None:
        raise PublishError("文件设置后没有出现标题/描述编辑区")


def _fill_fields(page: Page, content: Content, selectors: dict[str, list[str]]) -> None:
    if content.title:
        title_input = sel.find_visible(page, page, selectors["title_input"], timeout=3)
        if title_input is not None:
            title_input.fill(content.title)
        else:
            content.warnings.append("页面上没有找到标题输入框，已跳过标题")

    caption = content.full_caption
    if caption:
        editor = sel.find_visible(page, page, selectors["caption_editor"], timeout=5)
        if editor is None:
            raise PublishError("页面上没有找到作品描述编辑区")
        sel.fill_editor(page, editor, caption)
    page.wait_for_timeout(500)


def _wait_publishable(page: Page, selectors: dict[str, list[str]], *, timeout_s: float) -> Locator:
    deadline = time.monotonic() + max(10.0, timeout_s)
    while time.monotonic() < deadline:
        try:
            if sel.has_visible_spec(page, selectors["upload_failed"], timeout=0):
                raise PublishError("页面提示上传失败，请检查媒体文件后重试")
        except PublishError:
            raise
        except Exception:
            pass

        uploading = False
        try:
            uploading = sel.has_visible_spec(page, selectors["uploading"], timeout=0)
        except Exception:
            pass
        if not uploading:
            button = sel.find_visible(
                page, page, selectors["publish_button"], timeout=0, require_enabled=True
            )
            if button is not None:
                return button
        page.wait_for_timeout(1000)
    raise PublishError("等待上传完成/发布按钮可用超时；视频较大时可加 --timeout")


def _click_publish(page: Page, button: Locator, selectors: dict[str, list[str]]) -> None:
    sel.click(button)
    confirmations = 0
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if "content/manage" in (page.url or ""):
                return
        except Exception:
            pass

        try:
            if sel.has_visible_spec(page, selectors["success"], timeout=0):
                return
            if sel.has_visible_spec(page, selectors["publish_failed"], timeout=0):
                raise PublishError("页面提示发布失败，请查看截图与 HTML 快照")
        except PublishError:
            raise
        except Exception:
            pass

        if confirmations < 3:
            dialog = _visible_dialog(page)
            if dialog is None and sel.has_visible_spec(page, selectors["cover_dialog"], timeout=0):
                confirm = sel.find_visible(
                    page,
                    page,
                    selectors["confirm_button"] + ["role:button:发布"],
                    timeout=0,
                    require_enabled=True,
                )
                if confirm is not None:
                    sel.click(confirm)
                    confirmations += 1
                    page.wait_for_timeout(500)
                    continue
            if dialog is not None:
                confirm = sel.find_visible(
                    dialog,
                    page,
                    selectors["confirm_button"] + ["role:button:发布"],
                    timeout=0,
                    require_enabled=True,
                )
                if confirm is not None:
                    sel.click(confirm)
                    confirmations += 1
                    page.wait_for_timeout(500)
                    continue
        page.wait_for_timeout(500)
    raise PublishError("点击发布后没有检测到成功提示；请查看截图确认页面状态")


def _visible_dialog(page: Page) -> Locator | None:
    try:
        dialogs = page.locator("[role=dialog]")
        for index in range(min(dialogs.count(), 5)):
            dialog = dialogs.nth(index)
            if dialog.is_visible():
                return dialog
    except Exception:
        pass
    return None


def _screenshot(page: Page, art: Path, name: str) -> Path | None:
    path = art / name
    try:
        page.screenshot(path=str(path), full_page=False)
        return path
    except Exception:
        return None


def _record_failure(page: Page, art: Path) -> None:
    _screenshot(page, art, "failure.png")
    try:
        (art / "failure.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
    try:
        (art / "failure-url.txt").write_text(page.url or "", encoding="utf-8")
    except Exception:
        pass
