"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, config
from .content import Content
from .errors import (
    BrowserError,
    ContentError,
    DouyinRpaError,
    LoginTimeout,
    NotLoggedInError,
)
from .login import login_flow, status_flow
from .publish import new_artifacts_dir, publish_content


def _home(args: argparse.Namespace) -> Path:
    return config.home_dir(getattr(args, "home", None))


def _profile(args: argparse.Namespace) -> Path:
    explicit = getattr(args, "profile", None)
    return Path(explicit).expanduser() if explicit else _home(args) / "profile"


def _base_url(args: argparse.Namespace) -> str:
    return (getattr(args, "base_url", None) or config.default_base_url()).rstrip("/")


def _common_browser_args(parser: argparse.ArgumentParser, *, show_default: bool = False) -> None:
    parser.add_argument("--home", help="配置目录，默认 ~/.douyin-rpa")
    parser.add_argument("--profile", help="浏览器 profile 目录，默认 <home>/profile")
    parser.add_argument("--browser", choices=["chrome", "chromium"], default="chrome", help="默认使用系统 Chrome")
    parser.add_argument(
        "--show",
        action="store_true",
        default=show_default,
        help="显示浏览器窗口（默认无头；需要图形界面时使用）",
    )
    parser.add_argument("--base-url", default=None, help="创作者中心根地址，默认 https://creator.douyin.com")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="douyin-rpa",
        description="抖音创作者中心 RPA：人工扫码登录，复用会话自动发布视频/图文。",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="打开抖音登录页，保存二维码并等待扫码")
    _common_browser_args(login)
    login.add_argument("--timeout", type=float, default=300, help="等待扫码的秒数，默认 300")
    login.add_argument("--qr-out", default=None, help="二维码 PNG 保存路径，默认 <home>/login-qr.png")
    login.set_defaults(func=cmd_login)

    status = sub.add_parser("status", help="检查当前 profile 是否已登录")
    _common_browser_args(status)
    status.add_argument("--timeout", type=float, default=20, help="等待页面判断的秒数")
    status.add_argument("--json", action="store_true", help="以 JSON 输出")
    status.set_defaults(func=cmd_status)

    publish = sub.add_parser("publish", help="上传并发布视频或图文（默认只预览，不点发布）")
    _common_browser_args(publish)
    publish.add_argument("--manifest", help="内容清单 JSON 路径")
    publish.add_argument("--video", help="视频文件路径")
    publish.add_argument("--images", nargs="+", help="图文作品的多张图片路径")
    publish.add_argument("--title", default="", help="作品标题（使用 --manifest 时忽略）")
    publish.add_argument("--caption", default="", help="作品描述/文案（使用 --manifest 时忽略）")
    publish.add_argument("--tags", nargs="*", default=[], help="话题标签，可写多个（使用 --manifest 时忽略）")
    publish.add_argument(
        "--publish",
        action="store_true",
        help="真正点击「发布」。不加此参数时只上传并填好内容，等待人工确认",
    )
    publish.add_argument("--timeout", type=float, default=600, help="等待上传完成的秒数，默认 600")
    publish.add_argument("--selectors", help="选择器覆盖 JSON 文件")
    publish.add_argument("--artifacts-dir", help="截图/失败现场保存目录")
    publish.add_argument("--keep-open", action="store_true", help="配合 --show 在发布后保留窗口")
    publish.set_defaults(func=cmd_publish)
    return parser


def cmd_login(args: argparse.Namespace) -> int:
    qr_path = Path(args.qr_out).expanduser() if args.qr_out else _home(args) / "login-qr.png"
    result = login_flow(
        profile_dir=_profile(args),
        headless=not args.show,
        browser_name=args.browser,
        base_url=_base_url(args),
        qr_path=qr_path,
        timeout_s=args.timeout,
    )
    if result.already_logged_in:
        print(f"已经登录，无需扫码。profile：{_profile(args)}")
    else:
        print(f"登录成功，会话已保存到：{_profile(args)}")
    print(f"当前页面：{result.page_url}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    logged_in = status_flow(
        profile_dir=_profile(args),
        headless=not args.show,
        browser_name=args.browser,
        base_url=_base_url(args),
        timeout_s=args.timeout,
    )
    if args.json:
        print(json.dumps({"logged_in": logged_in, "profile": str(_profile(args))}, ensure_ascii=False))
    else:
        print("已登录" if logged_in else "未登录")
    return 0 if logged_in else 2


def _load_content(args: argparse.Namespace) -> Content:
    if args.manifest:
        if args.video or args.images:
            raise ContentError("--manifest 与 --video/--images 不能同时使用")
        return Content.from_manifest(args.manifest)
    return Content.from_paths(
        video=args.video,
        images=args.images,
        title=args.title,
        caption=args.caption,
        tags=args.tags,
    )


def cmd_publish(args: argparse.Namespace) -> int:
    content = _load_content(args)
    for warning in content.warnings:
        print(f"提示：{warning}", file=sys.stderr)

    try:
        selector_map = config.merge_selectors(config.load_selector_file(args.selectors))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ContentError(f"读取选择器文件失败：{exc}") from exc

    home = _home(args)
    artifacts = Path(args.artifacts_dir).expanduser() if args.artifacts_dir else new_artifacts_dir(home)
    dry_run = not args.publish

    from .browser import open_browser

    with open_browser(
        profile_dir=_profile(args),
        headless=not args.show,
        browser=args.browser,
        base_url=_base_url(args),
    ) as context:
        result = publish_content(
            context,
            content,
            artifacts_dir=artifacts,
            base_url=_base_url(args),
            dry_run=dry_run,
            keep_open=args.keep_open,
            selector_map=selector_map,
            timeout_s=args.timeout,
        )
        if args.keep_open and args.show:
            try:
                input("按 Enter 关闭浏览器...")
            except EOFError:
                pass

    if result.status == "published":
        print(f"已发布：{result.final_url}")
    else:
        print("预览完成：内容已上传并填好，但没有点击「发布」。")
        print("确认无误后加 --publish 再运行一次。")
    if result.screenshots:
        print("截图：")
        for screenshot in result.screenshots:
            print(f"  {screenshot}")
    print(f"现场目录：{result.artifacts_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except NotLoggedInError as exc:
        print(f"未登录：{exc}", file=sys.stderr)
        return 2
    except (LoginTimeout, ContentError, BrowserError, DouyinRpaError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        if getattr(exc, "artifacts_dir", None):
            print(f"失败现场：{exc.artifacts_dir}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
