# easytrader-order-service

本地接收派单、做风控、自动下单买卖的 A 股服务程序。基于
[easytrader](https://pypi.org/project/easytrader/) 封装，默认使用内置模拟盘，
确认无误后再切换到 Windows 券商客户端实盘。

> **重要提示**
>
> - 本程序默认 **paper + dry_run=true**，只做本地模拟成交，不会连接任何券商、不会下真实委托。
> - 实盘涉及真金白银。请先用模拟盘和小额资金验证，确认代码、价格、数量、交易时段、T+1
>   和券商规则都正确后再放大。程序按“尽力而为”执行，不对成交结果、盈亏或券商接口变化作保证。
> - A 股程序化交易可能需要向券商报备并遵守交易所规则；部分券商协议限制第三方客户端自动化。
>   使用前请自行确认合规要求，风险自负。
> - `easytrader` 的上游 GitHub 仓库已按监管要求下架（仓库只剩移除说明），但 PyPI 上仍有
>   `0.23.7` 版本。实盘依赖该包；如果未来 PyPI 也无法安装，模拟盘功能不受影响。

## 功能

- **HTTP 派单入口**：`POST /api/v1/orders`，支持限价/市价、买卖、幂等
  `client_order_id`、API key 鉴权。
- **执行 worker**：单线程串行调用券商，避免 easytrader 的 GUI 自动化并发冲突。
- **前置风控**：交易时段、代码白/黑名单、整手校验、单笔/单日金额、单日笔数、
  可选涨跌停、账户可用资金/可卖数量校验。
- **两种 broker**：
  - `paper`：内置模拟账户，含佣金、最低佣金、印花税、过户费、T+1，可本地运行。
  - `easytrader`：Windows 上连接同花顺/银河/华泰/国金/广发/海通/雪球/miniQMT 等客户端。
- **持久化**：SQLite 保存订单与审计事件；服务重启会恢复未执行订单，并把“提交中”
  的订单标记为 `unknown`，**不会自动重试**，避免重复实盘委托。
- **控制面**：暂停/恢复执行（kill switch）、撤单、账户查询、订单/事件查询。
- **CLI**：生成配置、启动服务、提交派单、查订单/账户、健康检查、重置模拟账户。

## 工作流程

```text
派单方/策略
   │  POST /api/v1/orders  (X-API-Key)
   ▼
FastAPI 接收 ──► SQLite 落库 ──► 内存队列
                                    │
                             单线程 worker
                                    │
                     ┌──────────────┴──────────────┐
                     │ 风控：时段/代码/整手/金额/   │
                     │ 笔数/涨跌停/资金/持仓        │
                     └──────────────┬──────────────┘
                         通过        │        拒绝
                                    ▼          ▼
                              broker.submit   订单 rejected
                          ┌─────────┴─────────┐
                          ▼                   ▼
                    paper 即时成交      easytrader 券商客户端
                    （模拟资金/持仓）    （真实委托，Windows）
```

状态机：

```text
received → accepted → submitting → submitted ─┬→ filled
   │           │            │                  ├→ partially_filled
   │           │            └→ failed          └→ cancel_pending → canceled
   │           └→ rejected
   └→ canceled

submitting → unknown   # 提交时异常/服务重启，可能已报单，需人工核对
```

`submitted` 在实盘下表示“委托已提交”，最终成交状态以券商客户端为准；本项目不做
逐笔成交回报轮询。`unknown` 永远不会自动重试。

## 快速开始（模拟盘）

要求 Python 3.11+。

```bash
cd easytrader-order-service
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# 生成配置（自动写入随机 API key）
easytrader-service init-config --output config.toml

# 启动服务
easytrader-service serve --config config.toml
```

另开一个终端，提交一笔模拟买单：

```bash
easytrader-service submit --config config.toml \
  --symbol 000001 --side buy --price 10.50 --quantity 100
```

查看订单和账户：

```bash
easytrader-service orders --config config.toml
easytrader-service account --config config.toml
```

也可以用 curl：

```bash
KEY=$(grep '^api_key' config.toml | cut -d'"' -f2)
curl -s http://127.0.0.1:8000/api/v1/orders \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "client_order_id": "strategy-20261106-001",
    "symbol": "000001",
    "side": "buy",
    "order_type": "limit",
    "price": 10.50,
    "quantity": 100,
    "source": "my-strategy",
    "remark": "first paper order"
  }'
```

## 派单接口

`POST /api/v1/orders`，请求头 `X-API-Key: <server.api_key>`（也支持
`Authorization: Bearer <key>`）。

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `symbol` | 是 | 6 位 A 股代码；兼容 `SZ000001` / `000001.SZ`，统一存为 `000001` |
| `side` | 是 | `buy` / `sell` |
| `order_type` | 否 | `limit`（默认）/ `market` |
| `price` | 限价必填 | 限价委托价格 |
| `reference_price` | 市价必填 | 市价委托用于风控估算和模拟成交的参考价 |
| `quantity` | 是 | 股数，默认必须是 100 的整数倍 |
| `client_order_id` | 否 | 幂等键；同一 ID 重复提交只返回原订单，不会重复下单 |
| `prev_close` | 否 | 昨收价；启用 `price_band_check` 时用于涨跌停校验 |
| `source` | 否 | 派单方标识，便于审计 |
| `remark` | 否 | 备注 |

返回 `202`：

```json
{
  "order": {
    "id": "…",
    "client_order_id": "strategy-20261106-001",
    "symbol": "000001",
    "side": "buy",
    "order_type": "limit",
    "price": 10.5,
    "quantity": 100,
    "status": "received",
    "broker_order_id": null,
    "filled_quantity": 0,
    "filled_price": null,
    "message": null,
    "source": "my-strategy",
    "remark": "first paper order",
    "trading_date": "2026-11-06",
    "created_at": "2026-11-06T10:00:00+08:00",
    "updated_at": "2026-11-06T10:00:00+08:00",
    "submitted_at": null
  },
  "duplicate": false
}
```

worker 异步执行；用 `GET /api/v1/orders/{id}` 或 CLI `order --id` 查看结果。
同一 `client_order_id` 再次提交时返回原订单且 `duplicate=true`。

## 配置

完整带注释的模板见
[`src/easytrader_service/templates/config.example.toml`](src/easytrader_service/templates/config.example.toml)。
`init-config` 会生成同样内容并填入随机 key。关键项：

- `[server]`：监听地址、端口、`api_key`、日志级别。默认 `127.0.0.1`。
- `[database]`：SQLite 路径；相对路径按配置文件目录解析。
- `[trading]`：
  - `dry_run`：模拟盘为 `true`；实盘为 `false`。
  - `max_order_amount` / `max_daily_amount` / `max_daily_orders`：风控上限。
  - `quantity_step`：整手单位，默认 100。
  - `enforce_trading_session` / `sessions` / `weekdays` / `holidays`：交易时段。
    中国节假日每年不同，需自行维护 `holidays`。
  - `enforce_account_check`：用券商资金/持仓做下单前校验。
  - `price_band_check` / `price_band_pct`：可选涨跌停校验。
  - `t_plus_one`：模拟盘 T+1。
- `[fees]`：佣金、最低佣金、卖出印花税、过户费。
- `[broker]`：`paper` 或 `easytrader`。
- `[broker.paper]`：初始资金、模拟账户状态文件。
- `[broker.easytrader]`：券商类型、`xiadan.exe` 路径、`prepare` 文件、连接参数。

### 两把钥匙的实盘开关

为了避免误连真实账户，实盘必须**同时**满足：

```toml
[trading]
dry_run = false

[broker]
type = "easytrader"
```

只改其中一个会在启动时报配置错误。

## 接入 easytrader 实盘（Windows）

easytrader 通过 Windows GUI 自动化驱动券商客户端，因此：

1. 在 Windows 上安装并登录券商客户端（同花顺/银河/华泰/国金/广发/海通等），
   保持客户端运行，能看到下单界面。
2. 安装实盘依赖：

   ```powershell
   py -3.11 -m venv .venv
   .venv\Scripts\activate
   pip install -e ".[live]"
   ```

3. 修改 `config.toml`：

   ```toml
   [trading]
   dry_run = false

   [broker]
   type = "easytrader"

   [broker.easytrader]
   broker = "ths"                       # 或 universal_client / yh_client / gj_client / miniqmt …
   exe_path = "C:\\同花顺软件\\同花顺\\xiadan.exe"
   ```

   - 同花顺：`broker = "ths"`，`exe_path` 指向 `xiadan.exe`。
   - 通用同花顺：`universal_client`。
   - 银河 `yh_client`、华泰 `ht_client`、国金 `gj_client`、广发 `gf_client`、海通 `htzq_client`。
   - miniQMT：`broker = "miniqmt"`，并设置
     `connect_kwargs = { miniqmt_path = "D:\\国金证券QMT交易端\\userdata_mini", stock_account = "资金账号" }`。
   - 雪球：`broker = "xq"`，并设置 `prepare` 指向雪球配置文件。

4. 先做只读检查：

   ```powershell
   easytrader-service doctor --config config.toml
   ```

   它会连接客户端、读取资金/持仓、检查交易时段，但**不会下单**。

5. 启动服务，先用最小数量（如 100 股）验证一笔，确认券商客户端里出现委托，
   再逐步放开。

> 实盘注意：easytrader 的返回结果在不同券商客户端上格式不完全一致。本项目对无法识别
> 的结果按 `unknown` 处理，不会自动重试；请以券商客户端里的委托列表为准。`broker.easytrader.market_ttype`
> 仅部分券商支持；不确定时请只用限价单。

## 风控与安全

- 默认只监听 `127.0.0.1`，请勿直接暴露到公网。
- 所有业务接口要求 API key；`init-config` 生成随机 key，也可用 `ETS_API_KEY` 覆盖。
- 单笔/单日金额、单日笔数、代码白黑名单、整手、交易时段、账户资金/持仓多重校验。
- 提交异常或服务重启时，订单进入 `unknown`，不会自动重试，避免重复真实委托。
- 支持一键暂停执行：`POST /api/v1/control/pause` 或 `easytrader-service control pause`。
- 不要把 `config.toml`、数据库、模拟账户文件提交到 Git（已在 `.gitignore` 中忽略）。

## API

除 `/api/v1/health` 外都需要 API key。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/health` | 健康检查（无需鉴权） |
| POST | `/api/v1/orders` | 接收派单 |
| GET | `/api/v1/orders?status=&limit=` | 订单列表 |
| GET | `/api/v1/orders/{id}` | 订单详情 |
| GET | `/api/v1/orders/{id}/events` | 审计事件 |
| POST | `/api/v1/orders/{id}/cancel` | 撤单（排队中直接撤销；已报则请求券商撤单） |
| GET | `/api/v1/account` | 资金与持仓 |
| POST | `/api/v1/control/pause` | `{"paused": true/false, "reason": "…"}` |
| GET | `/api/v1/control/status` | 暂停状态、队列长度、订单计数 |

交互式文档：`http://127.0.0.1:8000/docs`（`server.enable_docs=false` 可关闭）。

## CLI

```bash
easytrader-service init-config --output config.toml
easytrader-service serve --config config.toml
easytrader-service doctor --config config.toml

easytrader-service submit --config config.toml \
  --symbol 600000 --side sell --price 8.88 --quantity 100

easytrader-service orders --config config.toml --status filled --limit 20
easytrader-service order --config config.toml --id <order-id>
easytrader-service cancel --config config.toml --id <order-id> --reason "manual"
easytrader-service account --config config.toml
easytrader-service control pause --config config.toml --reason "market open review"
easytrader-service control resume --config config.toml
easytrader-service status --config config.toml
easytrader-service paper-reset --config config.toml --yes
```

`submit/orders/account/cancel/control` 通过 HTTP 访问服务，需先启动 `serve`。
`doctor/paper-reset` 直接读本地配置和状态文件。

## 测试

```bash
pip install -e ".[dev]"
pytest
```

测试覆盖：配置校验、风控各分支、SQLite 状态机、模拟盘费用/T+1、easytrader 适配器
（用注入的假模块，不需要 Windows）、服务层幂等/恢复/失败分类、HTTP 鉴权与接口。

## 部署建议

- 模拟盘：Linux/Windows 均可，直接 `serve`；可用 systemd 或 Windows 任务计划。
- 实盘：必须运行在安装了券商客户端并已登录的 Windows 主机上；建议放在有 UPS、
  网络稳定的机器，并保留人工随时暂停/撤单的手段。
- 生产前至少做：模拟盘全流程、券商客户端只读 `doctor`、最小数量实盘、异常断网/重启演练。

## 已知限制

- 不做逐笔成交回报/持仓轮询；实盘订单最终状态以券商客户端为准。
- 交易日历不内置中国节假日，需要维护 `holidays`。
- 模拟盘按委托价即时成交，不模拟盘口深度、滑点或部分成交。
- easytrader 各券商客户端字段差异较大；适配器做尽力归一化，无法识别时按
  `unknown` 处理。
- 市价单、科创板/创业板/北交所价格笼子等规则需按券商实际规则自行确认。

## 目录结构

```text
easytrader-order-service/
├── pyproject.toml
├── README.md
├── src/easytrader_service/
│   ├── api.py                 # FastAPI 接口与鉴权
│   ├── service.py             # 订单服务/worker/撤单
│   ├── risk.py                # 前置风控
│   ├── store.py               # SQLite 订单/审计
│   ├── models.py              # 订单模型
│   ├── config.py              # TOML 配置
│   ├── fees.py                # 费用模型
│   ├── trading_calendar.py    # 交易时段
│   ├── cli.py                 # 命令行
│   ├── broker/
│   │   ├── paper.py           # 模拟盘
│   │   └── easytrader_broker.py
│   └── templates/config.example.toml
├── tests/
├── examples/dispatch_orders.py
└── scripts/demo.sh
```
