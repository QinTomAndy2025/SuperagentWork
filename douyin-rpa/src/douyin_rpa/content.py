"""内容清单解析与校验。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .errors import ContentError

VIDEO_KIND = "video"
IMAGES_KIND = "images"


@dataclass
class Content:
    """一篇待发布的作品。"""

    kind: str
    media: list[Path]
    title: str = ""
    caption: str = ""
    tags: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def full_caption(self) -> str:
        pieces: list[str] = []
        if self.caption.strip():
            pieces.append(self.caption.strip())
        if self.tags:
            pieces.append(" ".join(f"#{tag}" for tag in self.tags))
        return " ".join(pieces)

    def validate(self) -> None:
        if self.kind not in (VIDEO_KIND, IMAGES_KIND):
            raise ContentError(f"作品类型必须是 {VIDEO_KIND!r} 或 {IMAGES_KIND!r}，收到 {self.kind!r}")
        if not self.media:
            raise ContentError("没有指定要上传的媒体文件")
        for path in self.media:
            if not path.exists():
                raise ContentError(f"媒体文件不存在：{path}")
            if not path.is_file():
                raise ContentError(f"媒体路径不是文件：{path}")
            if path.stat().st_size == 0:
                raise ContentError(f"媒体文件为空：{path}")

        if self.kind == VIDEO_KIND:
            if len(self.media) != 1:
                raise ContentError("视频作品只能上传 1 个视频文件")
            ext = self.media[0].suffix.lower()
            if ext not in config.VIDEO_EXTENSIONS:
                raise ContentError(
                    f"不支持的视频格式 {ext or '(无后缀)'}，支持：{', '.join(sorted(config.VIDEO_EXTENSIONS))}"
                )
            if ext not in {".mp4", ".mov", ".webm"}:
                self.warnings.append(f"{ext} 不是抖音网页端最常用的格式，若上传失败请先转成 mp4")
            size = self.media[0].stat().st_size
            if size > config.MAX_VIDEO_BYTES_WARN:
                self.warnings.append(f"视频约 {size / 1024**3:.1f} GiB，超过 4 GiB 时网页端可能拒绝")
        else:
            if len(self.media) > config.MAX_IMAGES:
                raise ContentError(f"图文作品最多 {config.MAX_IMAGES} 张图片，当前 {len(self.media)} 张")
            bad = [p.name for p in self.media if p.suffix.lower() not in config.IMAGE_EXTENSIONS]
            if bad:
                raise ContentError(
                    f"不支持的图片格式：{', '.join(bad)}；支持：{', '.join(sorted(config.IMAGE_EXTENSIONS))}"
                )

        self.title = self.title.strip()
        self.caption = self.caption.strip()
        self.tags = _normalise_tags(self.tags)
        if len(self.title) > config.MAX_TITLE_CHARS:
            raise ContentError(f"标题最多 {config.MAX_TITLE_CHARS} 个字符，当前 {len(self.title)} 个")
        full = self.full_caption
        if len(full) > config.MAX_CAPTION_CHARS:
            raise ContentError(f"作品描述最多 {config.MAX_CAPTION_CHARS} 个字符，当前 {len(full)} 个")
        if self.kind == IMAGES_KIND and not self.title:
            self.warnings.append("图文作品通常需要标题，发布时若页面要求标题可能失败")

    def to_dict(self) -> dict:
        return {
            "type": self.kind,
            "media": [str(path) for path in self.media],
            "title": self.title,
            "caption": self.full_caption,
            "tags": list(self.tags),
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_manifest(cls, path: str | Path) -> "Content":
        manifest_path = Path(path).expanduser().resolve()
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ContentError(f"内容清单不存在：{manifest_path}") from exc
        except json.JSONDecodeError as exc:
            raise ContentError(f"内容清单不是合法 JSON：{exc}") from exc
        if not isinstance(data, dict):
            raise ContentError("内容清单必须是 JSON 对象")

        kind = str(data.get("type") or "").lower()
        if kind in ("image", "图文"):
            kind = IMAGES_KIND
        if kind in ("视频",):
            kind = VIDEO_KIND
        base = manifest_path.parent

        media: list[Path] = []
        if kind == VIDEO_KIND:
            value = data.get("video")
            if not value:
                raise ContentError("视频清单需要 video 字段")
            media = [_resolve(base, value)]
        elif kind == IMAGES_KIND:
            values = data.get("images")
            if not isinstance(values, list) or not values:
                raise ContentError("图文清单需要非空的 images 列表")
            media = [_resolve(base, value) for value in values]
        else:
            raise ContentError("内容清单需要 type 字段：video 或 images")

        tags = data.get("tags") or []
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ContentError("tags 必须是字符串列表")

        content = cls(
            kind=kind,
            media=media,
            title=str(data.get("title") or ""),
            caption=str(data.get("caption") or ""),
            tags=list(tags),
        )
        content.validate()
        return content

    @classmethod
    def from_paths(
        cls,
        *,
        video: str | None = None,
        images: list[str] | None = None,
        title: str = "",
        caption: str = "",
        tags: list[str] | None = None,
    ) -> "Content":
        if video and images:
            raise ContentError("--video 与 --images 不能同时使用")
        if not video and not images:
            raise ContentError("需要 --video、--images 或 --manifest 指定内容")
        kind = VIDEO_KIND if video else IMAGES_KIND
        media = [Path(video).expanduser()] if video else [Path(item).expanduser() for item in images or []]
        content = cls(
            kind=kind,
            media=media,
            title=title,
            caption=caption,
            tags=list(tags or []),
        )
        content.validate()
        return content


def _resolve(base: Path, value: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ContentError("媒体路径必须是非空字符串")
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path)


def _normalise_tags(tags: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = str(raw).strip().lstrip("#").strip()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        result.append(tag)
    return result
