# stock-quote-mcp

A 股盘中实时行情 MCP 服务：零第三方依赖，Python 3.10+ 即可运行；内置腾讯、新浪、东方财富三个公开数据源，按可用性自动切换。

> 本项目使用公开免费行情接口，不是交易所授权行情，没有 SLA，仅供研究与学习，不构成投资建议。
> 自动交易、资金决策请使用券商或交易所授权的行情源。

## 调研结论：哪个接口“最稳定”？

免费公开接口里不存在单点意义上的“最稳”。它们都没有 SLA，都可能因为风控、Header、限流或字段变更而暂时失效。
真正稳定的工程做法是：**选多个独立实现，做主备切换，并对失败源做冷却**，而不是把策略押在某一个域名上。

2026-11 实测与公开资料对比：

| 方案 | 数据 | 鉴权 / Header | 实测 p50 | 评价 |
| --- | --- | --- | --- | --- |
| 腾讯 `qt.gtimg.cn` / `web.ifzq.gtimg.cn` | 快照、五档、涨跌停、当日分时 | UA；建议带 `Referer: https://gu.qq.com/` | 82.9ms | 字段最全，含行情时间戳；GBK 文本，无官方文档，需防 WAF，作为主源 |
| 新浪 `hq.sinajs.cn` | 快照、五档 | **必须**带 `Referer: https://finance.sina.com.cn/`，否则 403 | 94.2ms | 老牌接口、字段直观；反爬更敏感，作为备源 |
| 东方财富 `push2.eastmoney.com` / `push2his.eastmoney.com` | 快照、当日分时、代码搜索 | UA，无强制 Referer | 29.3ms | JSON 最好解析、延迟最低；批量价格是按 2/3 位小数的整数，需要按标的类型还原；作为兜底与分时/搜索备源 |
| [pytdx](https://github.com/rainx/pytdx) / [mootdx](https://github.com/mootdx/mootdx) | 通达信 TCP 行情 | 无 HTTP 鉴权 | — | 不走 Web WAF，适合更高频；需要三方库和可用服务器列表，不满足本项目的零依赖目标 |
| [AkShare](https://github.com/akfamily/akshare) | 对上述门户接口的封装 | 无 | — | 上手快，但依赖多，上游接口变动时容易整体失效 |
| [Tushare Pro](https://tushare.pro/)、券商/交易所授权行情 | Level-1/Level-2 | Token、付费、资质 | — | 生产级稳定性的正解；有成本和合规要求 |

> 实测时间 2026-11-07 07:38（周六休市）。验证的是接口可用性、延迟、解析正确性和最近一个交易日的数据；
> 连续竞价时段的刷新行为仍需在交易日复测，详见[实测](#实测)。

**选择**：腾讯为主源（字段最全，有行情时间戳、涨跌停、五档），新浪为备源，东方财富为第三备源并负责分时/搜索兜底。
东方财富延迟最低，但批量快照缺少逐条时间戳、价格缩放规则也更易误读，因此不把它放在主源位置。
这样任何单一接口失效时，MCP 仍能返回行情，只是延迟和字段完整度可能下降。

## 稳定性设计

- **多源故障切换**：`腾讯 → 新浪 → 东方财富`；某源连续失败 2 次后冷却 30 秒，避免坏源把超时叠加到每次请求；成功一次即恢复。
- **请求隔离**：每个数据源 4 秒超时，异常只影响当前源，不会拖垮整个工具调用。
- **短缓存**：行情 2 秒、分时 10 秒、搜索 5 分钟。盘中轮询不会打爆接口，也不会返回过旧的数据。
- **批量查询**：一次最多 50 个代码；单只代码无行情时进入 `errors`，不影响其余代码。
- **来源可追溯**：每个结果带 `source`；腾讯快照还带 `timestamp`，可直接判断行情是否最新。
- **健康检查**：`check_data_sources` 工具（或 `stock-quote sources`）逐个探测三源并返回延迟、错误和冷却状态。

## MCP 工具

| 工具 | 参数 | 说明 |
| --- | --- | --- |
| `get_realtime_quotes` | `symbols: string[]` | 实时快照：最新价、涨跌、开高低、成交量/额、五档、涨跌停、时间戳 |
| `get_intraday_minutes` | `symbol: string` | 当日 1 分钟分时：价格、成交量（手）、成交额（元） |
| `get_market_overview` | — | 上证指数、深证成指、创业板指、科创50、沪深300、北证50 + 当前交易时段 |
| `search_symbols` | `keyword: string` | 按名称、拼音或代码搜索，返回标准代码（如 `600519.SH`） |
| `check_data_sources` | — | 探测腾讯/新浪/东方财富的可用性与延迟 |

`get_realtime_quotes` 返回示例（省略部分字段）：

```json
{
  "as_of": "2026-11-07T07:40:00+08:00",
  "source": "tencent",
  "elapsed_ms": 83.2,
  "from_cache": false,
  "count": 1,
  "quotes": [
    {
      "symbol": "600519.SH",
      "code": "600519",
      "market": "SH",
      "name": "贵州茅台",
      "price": 1453.0,
      "prev_close": 1452.0,
      "open": 1450.0,
      "high": 1457.72,
      "low": 1436.0,
      "change": 1.0,
      "change_pct": 0.07,
      "volume_lots": 42848.0,
      "amount_yuan": 6199528289.0,
      "turnover_pct": 0.34,
      "limit_up": 1597.2,
      "limit_down": 1306.8,
      "bids": [{"price": 1452.0, "volume_lots": 2.0}],
      "asks": [{"price": 1453.0, "volume_lots": 2.0}],
      "timestamp": "2026-11-06T16:14:58+08:00",
      "source": "tencent"
    }
  ],
  "errors": []
}
```

代码格式支持 `600519`、`sh600519`、`600519.SH`、`000001.SZ`、`bj430047`。
`000001` 默认指平安银行（`000001.SZ`）；上证指数请写 `sh000001`，也支持中文别名 `上证指数`、`沪深300`、`创业板指` 等。

## 安装与接入

要求 Python 3.10+，运行时不需要安装任何第三方包。

```bash
cd stock-quote-mcp

# 方式一：不安装，直接用 PYTHONPATH 运行
PYTHONPATH=src python3 -m stock_quote_mcp --check   # 三源自检
PYTHONPATH=src python3 -m stock_quote_mcp           # MCP stdio 服务

# 方式二：安装为命令
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
stock-quote-mcp --check
```

### DSH（DeepSeek Harness）

把 [`examples/dsh-mcp.yml`](examples/dsh-mcp.yml) 中的条目加到
`~/.dsh/profiles/<profile>/cordis.patch.yml`，把绝对路径替换成本仓库路径：

```yaml
- id: mcp-stock-quote
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: stockquote
    transport: stdio
    command: python3
    args: ['-m', 'stock_quote_mcp']
    cwd: /abs/path/stock-quote-mcp
    env:
      PYTHONPATH: /abs/path/stock-quote-mcp/src
    toolCallTimeoutMs: 15000
```

重载后工具会以 `mcp__stockquote__get_realtime_quotes` 等名称出现。

### Claude Desktop / Cursor 等 MCP 客户端

```json
{
  "mcpServers": {
    "stock-quote": {
      "command": "python3",
      "args": ["-m", "stock_quote_mcp"],
      "env": {"PYTHONPATH": "/abs/path/stock-quote-mcp/src"}
    }
  }
}
```

完整示例见 [`examples/claude-desktop-config.json`](examples/claude-desktop-config.json)。

### 命令行（也可以给 skill/脚本用）

```bash
export PYTHONPATH=src

python3 -m stock_quote_mcp.cli quotes 600519 000001 sh000001
python3 -m stock_quote_mcp.cli minutes 600519
python3 -m stock_quote_mcp.cli market
python3 -m stock_quote_mcp.cli search 茅台
python3 -m stock_quote_mcp.cli sources
```

任意子命令加 `--json` 输出完整 JSON，便于其他程序或 skill 消费。

## 实测

以下数据来自 [`docs/stability-probe.json`](docs/stability-probe.json)，由 `scripts/live_probe.py` 生成：

```bash
python3 scripts/live_probe.py --rounds 40 --interval 0.5 --json-out docs/stability-probe.json
```

**环境**：2026-11-07 07:38 CST（周六休市），40 轮/源，间隔 0.5 秒，标的为
600519、000001、000300、899050、510300。

| 数据源 | 成功 | p50 | p95 | 最大 | 错误 |
| --- | --- | --- | --- | --- | --- |
| 腾讯 | 40/40 | 82.9ms | 89.8ms | 94.5ms | 无 |
| 新浪 | 40/40 | 94.2ms | 100.8ms | 106.6ms | 无 |
| 东方财富 | 40/40 | 29.3ms | 34.0ms | 47.6ms | 无 |

> 延迟包含 DNS/TLS 建连（每次请求独立连接），不是纯服务端耗时。休市时返回的是最近交易日收盘快照。

**价格一致性**：三源在 5 个标的上的最大价差为 0.01 个基点（000300 的四舍五入差异），其余完全一致。

**分时完整性**：

| 源 | 日期 | 1 分钟 K 线 | 累计成交量（手） | 说明 |
| --- | --- | --- | --- | --- |
| 腾讯 | 2026-11-06 | 267 | 42864 | 含 15:00–15:30 盘后成交 |
| 东方财富 | 2026-11-06 | 241 | 42848 | 止于 15:00 |

**官方 SDK 互操作**：用 `@modelcontextprotocol/sdk` 1.32.2 的 `Client` + `StdioClientTransport`
连接本服务，`initialize`、`tools/list`、`tools/call`、`structuredContent`、`isError` 均通过；
复现脚本见 [`scripts/interop_check.cjs`](scripts/interop_check.cjs)。

**单元测试**：53 个，全部基于离线 fixture 或注入的假数据源，不依赖网络：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

覆盖代码归一化、三源解析、分时、搜索、故障切换/冷却/缓存、MCP 协议帧和工具参数校验。

### 尚未覆盖

- **连续竞价时段的刷新**：本次探测在周六休市时进行，无法验证 9:30–15:00 的实时推送节奏。
  交易日复测只需再运行一次上面的 `live_probe.py`；行情返回的 `timestamp` 可直接判断新鲜度。
- **新浪分时**：新浪没有稳定的公开分时接口，分时只用腾讯和东方财富。
- **历史 K 线**：只提供当日分时，不做历史行情存储。

## 已知限制

- 免费接口没有 SLA，可能限流、返回风控页或修改字段。建议轮询间隔不低于 2 秒，本项目默认缓存 2 秒。
- A 股只覆盖上交所、深交所、北交所的股票、ETF 和指数；不支持港美股、Level-2 逐笔、历史 K 线。
- 交易时段（`session.phase`）只按北京时间与工作日推断，不识别法定节假日；请以行情 `timestamp` 为准。
- 东方财富批量快照没有可靠的逐条时间戳，且价格是整数，按股票 2 位、ETF/LOF 3 位小数还原；
  与腾讯/新浪的字段完整度不完全一致。
- 不同数据源的成交量单位、盘后成交范围可能不同（例如腾讯分时包含 15:00–15:30 盘后数据）。
- 本项目不构成投资建议，也不对行情准确性、可用性作任何担保。

## 目录结构

```text
stock-quote-mcp/
├── src/stock_quote_mcp/
│   ├── sources.py       # 腾讯/新浪/东方财富解析与请求
│   ├── pool.py          # 多源切换、冷却、缓存、健康状态
│   ├── tools.py         # 5 个 MCP 工具与 JSON Schema
│   ├── symbols.py       # 600519 / sh600519 / 600519.SH / 指数别名
│   ├── server.py        # 零依赖 MCP stdio 服务
│   ├── cli.py           # 命令行
│   └── http.py          # 超时与错误封装
├── tests/               # 53 个离线单元测试 + fixture
├── scripts/
│   ├── live_probe.py    # 真实网络探测/延迟/一致性
│   └── interop_check.cjs# 官方 MCP SDK 互操作检查
├── examples/            # DSH / Claude Desktop 配置
└── docs/                # 实测原始数据
```

## 参考

- 腾讯云开发者社区：[拒绝被封 IP！盘点 Python 获取实时股票行情的几种技术方案](https://developer.cloud.tencent.cn/article/2659683)
- [pqquotation](https://github.com/bushuhui/pqquotation)：新浪/腾讯/东方财富多源轮询与健康检查的同类实现
- [MCP 规范](https://modelcontextprotocol.io/specification/2025-06-18)
- [pytdx](https://github.com/rainx/pytdx)、[mootdx](https://github.com/mootdx/mootdx)、[AkShare](https://github.com/akfamily/akshare)、[Tushare Pro](https://tushare.pro/)
