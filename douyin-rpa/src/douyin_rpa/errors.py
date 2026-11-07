"""CLI 与发布流程使用的异常类型。"""

from __future__ import annotations

from pathlib import Path


class DouyinRpaError(Exception):
    """本项目所有可预期错误的基类。"""


class BrowserError(DouyinRpaError):
    """浏览器无法启动。"""


class ContentError(DouyinRpaError):
    """内容清单或媒体文件不合法。"""


class NotLoggedInError(DouyinRpaError):
    """当前浏览器配置里没有可用的抖音登录会话。"""


class LoginTimeout(DouyinRpaError):
    """等待扫码登录超时。"""


class PublishError(DouyinRpaError):
    """发布流程在页面上的某一步失败。"""

    def __init__(self, message: str, artifacts_dir: Path | None = None):
        super().__init__(message)
        self.artifacts_dir = artifacts_dir
