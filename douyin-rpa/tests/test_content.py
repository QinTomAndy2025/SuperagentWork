from __future__ import annotations

import json
from pathlib import Path

import pytest

from douyin_rpa.content import Content
from douyin_rpa.errors import ContentError


def write(tmp_path: Path, name: str, data: bytes = b"x") -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_manifest_video_resolves_relative_media_and_tags(tmp_path):
    video = write(tmp_path, "media/clip.mp4", b"\x00\x01")
    manifest = tmp_path / "content.json"
    manifest.write_text(
        json.dumps(
            {
                "type": "video",
                "video": "media/clip.mp4",
                "title": "今天的作品",
                "caption": "记录生活",
                "tags": ["日常", "#vlog", "日常", ""],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    content = Content.from_manifest(manifest)

    assert content.kind == "video"
    assert content.media == [video]
    assert content.full_caption == "记录生活 #日常 #vlog"
    assert content.warnings == []


def test_manifest_images(tmp_path):
    first = write(tmp_path, "a.jpg")
    second = write(tmp_path, "b.png")
    manifest = tmp_path / "content.json"
    manifest.write_text(
        json.dumps({"type": "images", "images": ["a.jpg", "b.png"], "title": "图文标题"}),
        encoding="utf-8",
    )

    content = Content.from_manifest(manifest)

    assert content.kind == "images"
    assert content.media == [first, second]
    assert content.full_caption == ""


def test_missing_media_is_rejected(tmp_path):
    with pytest.raises(ContentError, match="不存在"):
        Content.from_paths(video=str(tmp_path / "missing.mp4"))


def test_wrong_kind_is_rejected(tmp_path):
    video = write(tmp_path, "clip.mp4")
    with pytest.raises(ContentError, match="类型"):
        Content(kind="audio", media=[video]).validate()


def test_video_requires_exactly_one_file(tmp_path):
    first = write(tmp_path, "a.mp4")
    second = write(tmp_path, "b.mp4")
    with pytest.raises(ContentError, match="1 个视频"):
        Content(kind="video", media=[first, second]).validate()


def test_images_limit(tmp_path):
    images = [write(tmp_path, f"{index}.jpg") for index in range(36)]
    with pytest.raises(ContentError, match="最多 35"):
        Content(kind="images", media=images).validate()


def test_title_and_caption_limits(tmp_path):
    video = write(tmp_path, "clip.mp4")
    with pytest.raises(ContentError, match="标题最多"):
        Content(kind="video", media=[video], title="标" * 31).validate()
    with pytest.raises(ContentError, match="描述最多"):
        Content(kind="video", media=[video], caption="文" * 1001).validate()


def test_unsupported_image_extension(tmp_path):
    image = write(tmp_path, "a.gif")
    with pytest.raises(ContentError, match="图片格式"):
        Content(kind="images", media=[image]).validate()


def test_video_only_mode_reports_warning_for_unusual_container(tmp_path):
    video = write(tmp_path, "clip.avi")
    content = Content.from_paths(video=str(video))
    assert any("mp4" in warning for warning in content.warnings)


def test_cli_paths_mode_requires_media():
    with pytest.raises(ContentError, match="--video"):
        Content.from_paths()
