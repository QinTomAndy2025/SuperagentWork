"""Command-line interface for the order service."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import __version__
from .broker import build_broker
from .broker.paper import PaperBroker
from .config import config_template_text, load_config
from .errors import BrokerError, ConfigError
from .models import TERMINAL_STATUSES
from .trading_calendar import TradingCalendar
from .timeutil import now_china

_TERMINAL = {status.value for status in TERMINAL_STATUSES}


class CliError(Exception):
    pass


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


class ApiClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        data = None
        headers = {"X-API-Key": self.api_key, "Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            detail: Any = body
            try:
                parsed = json.loads(body)
                detail = parsed.get("detail", parsed)
            except json.JSONDecodeError:
                pass
            raise CliError(f"HTTP {exc.code} {url}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise CliError(f"无法连接服务 {url}: {exc.reason}") from exc
        if not body.strip():
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise CliError(f"服务返回了非 JSON 内容：{body[:200]}") from exc


def _base_url(config, override: str | None) -> str:
    if override:
        return override.rstrip("/")
    host = config.server.host
    if host in {"0.0.0.0", "::", ""}:
        host = "127.0.0.1"
    return f"http://{host}:{config.server.port}"


def _client(config, override: str | None) -> ApiClient:
    return ApiClient(_base_url(config, override), config.server.api_key)


# ------------------------------------------------------------------- commands
def cmd_init_config(args: argparse.Namespace) -> int:
    path = Path(args.output).expanduser().resolve()
    if path.exists() and not args.force:
        raise CliError(f"配置文件已存在：{path}（使用 --force 覆盖）")
    text = config_template_text()
    api_key = secrets.token_urlsafe(24)
    if 'api_key = ""' not in text:
        raise CliError("配置模板缺少 api_key 占位符，请检查安装是否完整")
    text = text.replace('api_key = ""', f'api_key = "{api_key}"', 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    print(f"已生成配置文件：{path}")
    print(f"API key：{api_key}")
    print("默认是模拟盘（paper + dry_run=true），不会连接真实券商。")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    config.validate_runtime()
    from .api import create_app

    app = create_app(config, start_worker=not args.no_worker)
    import uvicorn

    host = args.host or config.server.host
    port = args.port or config.server.port
    print(f"easytrader-order-service {__version__} listening on http://{host}:{port}")
    print(f"broker={config.broker.type} dry_run={config.trading.dry_run}")
    uvicorn.run(app, host=host, port=port, log_level=config.server.log_level.lower())
    return 0


def _build_order_payload(args: argparse.Namespace) -> dict[str, Any]:
    price = args.price
    reference_price = args.reference_price
    if args.order_type == "limit":
        if price is None:
            raise CliError("限价委托必须提供 --price")
    else:
        if reference_price is None:
            reference_price = price
        if reference_price is None:
            raise CliError("市价委托必须提供 --reference-price 或 --price")
    payload: dict[str, Any] = {
        "symbol": args.symbol,
        "side": args.side,
        "order_type": args.order_type,
        "quantity": args.quantity,
        "source": args.source,
        "remark": args.remark,
    }
    if args.order_type == "limit":
        payload["price"] = price
    else:
        payload["reference_price"] = reference_price
    if args.client_order_id:
        payload["client_order_id"] = args.client_order_id
    return payload


def cmd_submit(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    client = _client(config, args.url)
    response = client.request("POST", "/api/v1/orders", _build_order_payload(args))
    order = response["order"]
    print(
        f"{'重复委托' if response.get('duplicate') else '已受理'} "
        f"{order['id']} status={order['status']}"
    )

    if args.wait > 0 and order["status"] not in _TERMINAL:
        deadline = time.time() + args.wait
        while time.time() < deadline:
            time.sleep(0.2)
            order = client.request("GET", f"/api/v1/orders/{order['id']}")
            if order["status"] in _TERMINAL:
                break
        if order["status"] not in _TERMINAL:
            print(f"等待超时，订单当前状态：{order['status']}", file=sys.stderr)
    _print_json(order)

    if order["status"] in {"rejected", "failed", "unknown"}:
        return 2
    return 0


def cmd_orders(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    client = _client(config, args.url)
    query = f"?limit={args.limit}"
    if args.status:
        query += f"&status={args.status}"
    _print_json(client.request("GET", f"/api/v1/orders{query}"))
    return 0


def cmd_order(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    client = _client(config, args.url)
    _print_json(client.request("GET", f"/api/v1/orders/{args.id}"))
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    client = _client(config, args.url)
    _print_json(
        client.request("POST", f"/api/v1/orders/{args.id}/cancel", {"reason": args.reason})
    )
    return 0


def cmd_account(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    client = _client(config, args.url)
    _print_json(client.request("GET", "/api/v1/account"))
    return 0


def cmd_control(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    client = _client(config, args.url)
    if args.action in {"pause", "resume"}:
        payload = {"paused": args.action == "pause", "reason": args.reason}
        _print_json(client.request("POST", "/api/v1/control/pause", payload))
    else:
        _print_json(client.request("GET", "/api/v1/control/status"))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    client = _client(config, args.url)
    _print_json(client.request("GET", "/api/v1/control/status"))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    config.validate_runtime()
    calendar = TradingCalendar(config.trading)
    is_open, session_reason = calendar.is_open(now_china())
    broker = build_broker(config, persist_paper=False)
    try:
        broker.connect()
        account = broker.get_account()
    finally:
        broker.close()
    report = {
        "version": __version__,
        "broker": broker.name,
        "dry_run": config.trading.dry_run,
        "database": config.database.path,
        "paper_state": (
            config.broker.paper.state_path if config.broker.type == "paper" else None
        ),
        "trading_session_open": is_open,
        "trading_session_note": session_reason,
        "account": {
            "cash_available": account.cash_available,
            "total_assets": account.total_assets,
            "positions": [
                {
                    "symbol": item.symbol,
                    "quantity": item.quantity,
                    "available_quantity": item.available_quantity,
                }
                for item in account.positions
            ],
        },
    }
    _print_json(report)
    if not is_open:
        print(f"提示：{session_reason}", file=sys.stderr)
    return 0


def cmd_paper_reset(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if config.broker.type != "paper":
        raise CliError("只有 broker.type=paper 时才能重置模拟账户")
    if not args.yes:
        raise CliError("这会清空模拟账户资金与持仓；确认请加 --yes")
    broker = PaperBroker(
        config.broker.paper, config.fees, t_plus_one=config.trading.t_plus_one
    )
    broker.reset()
    print(f"已重置模拟账户：{config.broker.paper.state_path}")
    return 0


# --------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="easytrader-service",
        description="本地接收派单并自动下单的 easytrader 服务",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("ETS_CONFIG", "config.toml"),
        help="配置文件路径（默认 config.toml 或 ETS_CONFIG）",
    )
    parser.add_argument("--version", action="version", version=__version__)

    # Allow --config both before and after the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        default=argparse.SUPPRESS,
        help="配置文件路径（也可写在子命令前）",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-config", parents=[common], help="生成带随机 API key 的配置文件")
    init.add_argument("--output", default="config.toml")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init_config)

    serve = sub.add_parser("serve", parents=[common], help="启动 HTTP 服务与执行 worker")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--no-worker", action="store_true", help="只接收订单，不执行")
    serve.set_defaults(func=cmd_serve)

    submit = sub.add_parser("submit", parents=[common], help="提交一笔派单")
    submit.add_argument("--symbol", required=True)
    submit.add_argument("--side", required=True, choices=["buy", "sell"])
    submit.add_argument("--quantity", required=True, type=int)
    submit.add_argument("--price", type=float)
    submit.add_argument("--reference-price", type=float)
    submit.add_argument("--order-type", choices=["limit", "market"], default="limit")
    submit.add_argument("--client-order-id")
    submit.add_argument("--source", default="cli")
    submit.add_argument("--remark", default="")
    submit.add_argument("--wait", type=float, default=5.0, help="等待终态秒数，0 表示不等待")
    submit.add_argument("--url", help="覆盖服务地址，例如 http://127.0.0.1:8000")
    submit.set_defaults(func=cmd_submit)

    orders = sub.add_parser("orders", parents=[common], help="查询订单列表")
    orders.add_argument("--status")
    orders.add_argument("--limit", type=int, default=50)
    orders.add_argument("--url")
    orders.set_defaults(func=cmd_orders)

    order = sub.add_parser("order", parents=[common], help="查询单笔订单")
    order.add_argument("--id", required=True)
    order.add_argument("--url")
    order.set_defaults(func=cmd_order)

    cancel = sub.add_parser("cancel", parents=[common], help="撤销排队中或已报订单")
    cancel.add_argument("--id", required=True)
    cancel.add_argument("--reason", default="")
    cancel.add_argument("--url")
    cancel.set_defaults(func=cmd_cancel)

    account = sub.add_parser("account", parents=[common], help="查询账户资金与持仓")
    account.add_argument("--url")
    account.set_defaults(func=cmd_account)

    control = sub.add_parser("control", parents=[common], help="暂停/恢复/查看执行状态")
    control.add_argument("action", choices=["pause", "resume", "status"])
    control.add_argument("--reason", default="")
    control.add_argument("--url")
    control.set_defaults(func=cmd_control)

    status = sub.add_parser("status", parents=[common], help="查看执行状态")
    status.add_argument("--url")
    status.set_defaults(func=cmd_status)

    doctor = sub.add_parser("doctor", parents=[common], help="检查配置、账户和交易时段")
    doctor.set_defaults(func=cmd_doctor)

    reset = sub.add_parser("paper-reset", parents=[common], help="重置模拟账户（危险）")
    reset.add_argument("--yes", action="store_true")
    reset.set_defaults(func=cmd_paper_reset)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except (CliError, ConfigError, BrokerError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
