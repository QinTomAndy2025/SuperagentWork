from __future__ import annotations

import time
from pathlib import Path

import pytest

from conftest import login
from douyin_rpa.content import Content
from douyin_rpa.errors import NotLoggedInError
from douyin_rpa.publish import publish_content


def make_video(tmp_path: Path, name: str = "clip.mp4") -> Path:
    path = tmp_path / name
    path.write_bytes(b"\x00" * 4096)
    return path


def make_image(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"\xff\xd8\xff" + b"\x00" * 128)
    return path


def wait_for_published(server, expected: int = 1, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(server.published) >= expected:
            return
        time.sleep(0.05)
    raise AssertionError(f"服务端没有收到发布请求，已收到 {server.published!r}")


def test_publish_requires_login(browser_context, mock_server, tmp_path):
    content = Content.from_paths(video=str(make_video(tmp_path)), caption="文案")

    with pytest.raises(NotLoggedInError, match="douyin-rpa login"):
        publish_content(
            browser_context,
            content,
            artifacts_dir=tmp_path / "art",
            base_url=mock_server.url,
            dry_run=False,
            timeout_s=20,
        )


def test_dry_run_fills_but_does_not_publish(browser_context, mock_server, tmp_path):
    login(browser_context, mock_server)
    content = Content.from_paths(
        video=str(make_video(tmp_path)),
        title="预览标题",
        caption="预览文案",
        tags=["日常"],
    )

    result = publish_content(
        browser_context,
        content,
        artifacts_dir=tmp_path / "art",
        base_url=mock_server.url,
        dry_run=True,
        timeout_s=30,
    )

    assert result.status == "preview"
    assert mock_server.published == []
    assert result.screenshots and result.screenshots[0].exists()
    assert (tmp_path / "art" / "01-filled.png").exists()


def test_publish_video(browser_context, mock_server, tmp_path):
    login(browser_context, mock_server)
    content = Content.from_paths(
        video=str(make_video(tmp_path)),
        title="今天的作品",
        caption="记录生活",
        tags=["日常", "vlog"],
    )

    result = publish_content(
        browser_context,
        content,
        artifacts_dir=tmp_path / "art",
        base_url=mock_server.url,
        dry_run=False,
        timeout_s=30,
    )

    assert result.status == "published"
    assert "content/manage" in result.final_url or result.final_url.startswith(mock_server.url)
    wait_for_published(mock_server)
    payload = mock_server.published[0]
    assert payload["type"] == "video"
    assert payload["title"] == "今天的作品"
    assert payload["caption"] == "记录生活 #日常 #vlog"
    assert payload["files"] == ["clip.mp4"]
    assert result.screenshots
    assert (tmp_path / "art" / "02-published.png").exists()


def test_publish_images(browser_context, mock_server, tmp_path):
    login(browser_context, mock_server)
    content = Content.from_paths(
        images=[str(make_image(tmp_path, "1.jpg")), str(make_image(tmp_path, "2.png"))],
        title="图文作品",
        caption="两张图",
    )

    result = publish_content(
        browser_context,
        content,
        artifacts_dir=tmp_path / "art",
        base_url=mock_server.url,
        dry_run=False,
        timeout_s=30,
    )

    assert result.status == "published"
    wait_for_published(mock_server)
    payload = mock_server.published[0]
    assert payload["type"] == "image"
    assert payload["files"] == ["1.jpg", "2.png"]
    assert payload["title"] == "图文作品"


def test_publish_handles_confirm_dialog(browser_context, mock_server, tmp_path):
    login(browser_context, mock_server)
    mock_server.confirm_mode = True
    content = Content.from_paths(video=str(make_video(tmp_path)), caption="需要确认")

    result = publish_content(
        browser_context,
        content,
        artifacts_dir=tmp_path / "art",
        base_url=mock_server.url,
        dry_run=False,
        timeout_s=30,
    )

    assert result.status == "published"
    wait_for_published(mock_server)
    assert mock_server.published[0]["caption"] == "需要确认"
