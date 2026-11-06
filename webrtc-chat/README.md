# WebRTC 局域网聊天与音视频通话

一个可自托管的小型 WebRTC 应用：浏览器之间进行**点对点音视频通话**，并提供**文字聊天**。仓库里包含信令服务器、纯静态 Web 客户端、systemd 部署脚本和端到端测试。

- 房间制：同一房间内的设备自动建立连接
- 文字聊天：经信令服务器实时转发
- 音视频：WebRTC 点对点传输，支持麦克风/摄像头开关
- 局域网部署：HTTPS + 自签名证书（浏览器要求安全上下文才能访问摄像头）
- TURN 中继：支持通过环境变量接入 coturn，跨网段/严格 NAT 时可回退中继
- 零前端框架、无外部 CDN 依赖，断网局域网也能用

## 架构

```
┌──────────────┐        HTTPS / WebSocket        ┌──────────────────┐
│  浏览器 A     │ ◄─────────────────────────────► │  Node 信令服务器   │
│  (Web 客户端) │                                │  + 静态页面        │
└──────┬───────┘                                └────────┬─────────┘
       │                                                 │
       │            WebRTC（P2P 音视频）                   │
       │◄════════════════════════════════════════════════►│
       │                                                 │
┌──────┴───────┐        HTTPS / WebSocket        ┌────────┴─────────┐
│  浏览器 B     │ ◄─────────────────────────────► │  coturn (STUN/TURN)│
└──────────────┘                                └──────────────────┘
```

- 信令服务器：`server.js`，负责房间管理、SDP/ICE 转发、文字消息广播
- 客户端：`public/`，原生 HTML/CSS/JS，使用 `RTCPeerConnection`（mesh，每对用户一条连接）
- TURN：独立部署的 coturn，通过 `TURN_URLS` / `TURN_USER` / `TURN_PASSWORD` 注入

## 目录结构

```
webrtc-chat/
├── server.js              # 信令服务器 + 静态文件服务
├── public/                # Web 客户端（index.html / styles.css / app.js）
├── test/server.test.js    # 信令服务器单元/集成测试
├── scripts/e2e.js         # Playwright 双浏览器端到端测试
├── deploy/install.sh      # Ubuntu 一键安装/更新（systemd + TLS）
└── docs/                  # 截图与部署记录
```

## 本地运行

需要 Node.js 18+（推荐 22）。

```bash
cd webrtc-chat
npm install
npm start
```

打开 <http://localhost:8443>。没有配置证书时服务器使用 HTTP；浏览器把 `localhost` 视为安全上下文，所以本机仍可授权摄像头/麦克风。局域网其他设备访问必须使用 HTTPS。

用两个浏览器窗口（或一个普通窗口 + 一个无痕窗口）输入相同房间号即可联调。

## 部署到局域网（Ubuntu）

### 1. 安装 Node.js 22

```bash
sudo apt-get install -y curl xz-utils ca-certificates
curl -fsSL https://nodejs.org/dist/v22.22.0/node-v22.22.0-linux-x64.tar.xz -o /tmp/node.tar.xz
sudo mkdir -p /opt/node
sudo tar -xJf /tmp/node.tar.xz --strip-components=1 -C /opt/node
sudo ln -sf /opt/node/bin/node /usr/local/bin/node
sudo ln -sf /opt/node/bin/npm /usr/local/bin/npm
sudo ln -sf /opt/node/bin/npx /usr/local/bin/npx
node -v   # v22.22.0
```

### 2. 部署 coturn（STUN/TURN）

```bash
sudo apt-get install -y coturn
sudo sed -i 's/^#TURNSERVER_ENABLED=1/TURNSERVER_ENABLED=1/' /etc/default/coturn
```

`/etc/turnserver.conf` 最小示例（按需修改密码和网段）：

```ini
listening-port=3478
tls-listening-port=5349
fingerprint
lt-cred-mech
user=webrtc:CHANGE_ME
realm=webrtc.lan
min-port=49160
max-port=49200
```

```bash
sudo systemctl enable --now coturn
sudo systemctl status coturn
```

同网段直连通常不需要 TURN；但配置后，跨 VLAN、严格 NAT 或企业网络下更稳定。

### 3. 安装本应用

```bash
sudo TURN_URLS="turn:192.168.1.110:3478?transport=udp,turn:192.168.1.110:3478?transport=tcp" \
     TURN_USER="webrtc" TURN_PASSWORD="CHANGE_ME" \
     LAN_IP="192.168.1.110" \
     ./deploy/install.sh
```

脚本会：

1. 复制应用与生产依赖到 `/opt/webrtc-chat`
2. 写入 `/etc/webrtc-chat/webrtc-chat.env`（权限 600）
3. 生成包含 `LAN_IP` SAN 的自签名 TLS 证书到 `/etc/webrtc-chat/tls/`
4. 注册并启动 `webrtc-chat.service`（开机自启、失败重启）
5. 如果 ufw 已启用，放行 8443/8080 端口

完成后访问 `https://<LAN_IP>:8443`。首次打开浏览器会提示证书不受信任，选择“高级 → 继续访问”即可（局域网自签名证书的正常现象）。

常用运维命令：

```bash
sudo systemctl status webrtc-chat
sudo journalctl -u webrtc-chat -f
sudo systemctl restart webrtc-chat
```

修改配置（TURN 地址等）后编辑 `/etc/webrtc-chat/webrtc-chat.env` 并重启服务。

## 使用

1. 打开 `https://<LAN_IP>:8443`，信任自签名证书
2. 输入昵称和房间号，点击“进入房间”（房间号可从邀请链接自动带入）
3. 浏览器请求摄像头/麦克风权限；允许后即可看到本地画面
4. 另一台设备使用相同房间号进入，双方自动建立音视频连接
5. 文字聊天随时可用；底部按钮可开关麦克风/摄像头
6. “复制邀请链接”会生成带房间号的 URL，发给同局域网的人即可

如果拒绝了摄像头权限，仍可文字聊天；点击“重试音视频”可重新授权。使用 `https://` + 自签名证书时，如果浏览器仍阻止设备，请先确认地址栏显示“证书已接受”。

## 测试

信令服务器测试（Node 内置 test runner）：

```bash
npm test
```

端到端测试（需要 Playwright Chromium：`npx playwright install chromium`）：

```bash
# 本地
BASE_URL=http://localhost:8443 npm run e2e

# 已部署的局域网实例
BASE_URL=https://192.168.1.110:8443 npm run e2e
```

端到端测试会启动两个带**虚拟摄像头/麦克风**的 Chromium 页面，加入同一房间，验证：

- 双方 `RTCPeerConnection` 状态为 `connected`
- 远端收到至少 1 路音频和 1 路视频，且视频有实际尺寸
- 双向文字消息到达
- 截图输出到 `docs/screenshots/`

验证 TURN 中继是否真的可用（强制 `iceTransportPolicy: relay`，只用中继候选）：

```bash
BASE_URL=https://192.168.1.110:8443 npm run e2e -- --relay
```

该模式要求浏览器能访问 `TURN_URLS`；通过则说明 coturn 正常提供了 relay candidate。

## 配置项

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `8443` | HTTPS 端口 |
| `HTTP_PORT` | `8080` | HTTP 跳转端口；设为空字符串可关闭 |
| `TLS_CERT` / `TLS_KEY` | `certs/cert.pem`、`certs/key.pem` | 证书路径；不存在时退化为 HTTP |
| `STUN_URLS` | 空 | 逗号分隔的 STUN 地址，例如 `stun:192.168.1.110:3478` |
| `TURN_URLS` | 空 | 逗号分隔的 TURN 地址 |
| `TURN_USER` / `TURN_PASSWORD` | 空 | coturn 长期凭据 |
| `MAX_PEERS` | `8` | 单房间人数上限 |

`GET /api/ice` 会把 ICE 配置下发给浏览器；`GET /healthz` 返回房间数、在线人数和运行时长，便于探活。

## 安全与限制

- 面向**可信局域网**：没有账号体系、房间密码或权限控制。任何能访问页面的人都可以加入房间。暴露到公网前必须增加认证、房间口令、访客限流和审计。
- 自签名证书只适合内网；公网部署请使用受信任证书（例如 Let's Encrypt）。
- 文字消息经服务器转发且不持久化，刷新页面即丢失；音视频为 P2P，不落盘。
- mesh 拓扑下每个用户都要与其他用户建立连接，人数越多上行带宽压力越大，默认上限 8 人。
- TURN 凭据由服务端下发，属于共享凭据；生产环境应改为 coturn 的 `use-auth-secret` 临时凭据。
- 项目不做录制、屏幕共享、通话邀请推送等扩展功能。

## 故障排查

| 现象 | 排查 |
| --- | --- |
| 页面打不开 | `systemctl status webrtc-chat`；确认 8443 端口未被防火墙拦截 |
| 无法授权摄像头 | 必须用 `https://` 或 `localhost`；确认已接受自签名证书 |
| 有聊天无画面 | 检查 `RTCPeerConnection` 是否 `connected`；跨网段时确认 TURN 可达（UDP 3478） |
| 跨网段连不上 | 用 `npm run e2e -- --relay` 验证 TURN；检查 coturn 的 `external-ip`、防火墙和 `min-port/max-port` |
| 页面崩溃/白屏 | 打开浏览器控制台查看错误；`journalctl -u webrtc-chat` 查看服务端日志 |

## 当前部署

本仓库对应的演示部署在 `192.168.1.110`（Ubuntu 22.04 虚拟机）：

- 客户端：<https://192.168.1.110:8443>
- 信令服务：`webrtc-chat.service`（systemd，开机自启）
- TURN：`turn:192.168.1.110:3478`（复用该机已有的 coturn；凭据仅保存在服务器 `/etc/webrtc-chat/webrtc-chat.env`，不提交到仓库）

详见 [`docs/deployment.md`](docs/deployment.md)。
