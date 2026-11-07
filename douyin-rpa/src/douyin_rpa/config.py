"""路径、URL 与页面选择器默认值。

抖音创作者中心的 DOM 会随版本变化。这里的默认选择器按“候选列表”组织：
逐个尝试，命中第一个可见元素；页面改版时可以通过 ``--selectors`` 覆盖，
不必修改代码。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_BASE_URL = "https://creator.douyin.com"

# 登录后创作者中心的上传入口。
UPLOAD_PATH = "/creator-micro/content/upload"

# 内容限制（以抖音创作者中心页面上的计数为准，改版后可在 README 中更新）。
MAX_TITLE_CHARS = 30
MAX_CAPTION_CHARS = 1000
MAX_IMAGES = 35
MAX_VIDEO_BYTES_WARN = 4 * 1024**3

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".flv", ".mkv"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

# 默认选择器。字符串前缀：
#   css:...           CSS
#   text:...          包含文本
#   text!:...         文本完全相等
#   role:button:发布  无障碍角色 + 完全相等的名称
#   placeholder:...   placeholder 包含该文本的 input/textarea
DEFAULT_SELECTORS: dict[str, list[str]] = {
    "login_qr_tab": ["text:扫码登录", "text!:扫码登录"],
    "login_marker": ["text:扫码登录", "text:验证码登录", "text:密码登录"],
    "login_expired": ["text:二维码已失效", "text:二维码失效", "text:点击刷新"],
    "logged_in_hint": ["text!:发布作品", "text!:作品管理", "text!:数据中心"],
    "image_tab": ["text!:发布图文", "text:发布图文"],
    "title_input": [
        "placeholder:作品标题",
        "placeholder:标题",
        "css:input[placeholder*='标题']",
    ],
    "caption_editor": [
        "css:.zone-container [contenteditable='true']",
        "css:.zone-container",
        "css:[data-placeholder*='作品描述']",
        "css:[data-placeholder*='添加作品描述']",
        "css:div[contenteditable='true']",
        "css:textarea[placeholder*='作品描述']",
    ],
    "publish_button": [
        "role:button:发布",
        "css:.content-confirm-container button",
        "css:button:has-text('发布')",
    ],
    "uploading": ["text:上传中", "text:正在上传", "text:视频上传中"],
    "upload_failed": ["text:上传失败", "text:上传出错"],
    "success": ["text:发布成功", "text:作品发布成功"],
    "publish_failed": ["text:发布失败", "text:发布出错"],
    "confirm_button": [
        "role:button:确定",
        "role:button:确认发布",
        "text!:确定",
        "text!:确认发布",
    ],
    "cover_dialog": ["text:设置封面"],
}


def home_dir(explicit: str | os.PathLike[str] | None = None) -> Path:
    """返回配置/浏览器配置目录，默认 ``~/.douyin-rpa``。"""

    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("DOUYIN_RPA_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".douyin-rpa"


def default_base_url() -> str:
    return os.environ.get("DOUYIN_RPA_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def upload_url(base_url: str | None = None) -> str:
    return f"{(base_url or default_base_url()).rstrip('/')}{UPLOAD_PATH}"


def merge_selectors(overrides: dict[str, list[str]] | None) -> dict[str, list[str]]:
    """把用户覆盖项合并到默认选择器上（覆盖同名项，不修改默认值）。"""

    merged = {key: list(value) for key, value in DEFAULT_SELECTORS.items()}
    if overrides:
        for key, value in overrides.items():
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"选择器 {key!r} 必须是字符串列表")
            if not value:
                continue
            merged[key] = list(value)
    return merged


def load_selector_file(path: str | os.PathLike[str] | None) -> dict[str, list[str]]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("选择器文件必须是 JSON 对象")
    return data
