# WebRTC 手机 · 设备通话

一个可自托管的 WebRTC 应用：**手机用户**用手机号注册，**设备用户**（平板/Pad）注册后自动分配唯一的 **12 位设备号**；双方通过号码添加、**互相确认**后成为联系人，才能发起点对点音视频通话和文字聊天。

后端使用 **SQLite**（Node 内置 `node:sqlite`，无原生依赖）持久化用户、登录会话和联系人关系；界面适配手机横屏以及 Pad 横竖屏。

## 功能

- 手机用户：手机号 + 密码注册/登录，界面适配手机横屏
- 设备用户：设备注册后**自动激活**，服务端分配唯一 12 位设备号；界面适配 Pad 横竖屏
- 添加联系人
  - 设备用户输入手机号添加手机用户，手机用户确认
  - 手机用户输入 12 位设备号添加设备用户，设备用户确认
  - 双方互相添加时自动确认；未确认不能通话
- 通话：从通讯录一键呼叫，对方接听/拒绝；接听后建立 P2P 音视频，通话中可发文字
- 账号持久化：用户、会话、联系人写入 SQLite，重启不丢
- 实时状态：联系人在线状态、好友请求、来电弹窗
- 部署：HTTPS、自签名证书、systemd、TURN（coturn），支持域名和局域网 IP 双入口

## 账号与联系人规则

| 角色 | 登录标识 | 可添加 | 确认方 |
| --- | --- | --- | --- |
| 手机用户 | 11 位手机号 | 设备用户（12 位设备号） | 设备用户 |
| 设备用户 | 12 位设备号 | 手机用户（手机号） | 手机用户 |

- 注册后账号立即可用；设备账号不依赖短信或邮箱，注册即激活。
- 关系是**双向**的：请求方发送后进入“待确认”，对方确认后才出现在双方通讯录。
- 如果双方同时互相添加，第二次请求会自动确认。
- 只有已确认的联系人才能互相呼叫；通话室使用随机 UUID，不对外暴露房间列表。

## 架构

```
┌──────────────┐        HTTPS / WebSocket        ┌──────────────────────────┐
│ 手机浏览器    │ ◄─────────────────────────────► │ Node 信令服务器           │
│ / Pad 浏览器  │                                │ + 静态客户端 + SQLite      │
└──────┬───────┘                                └────────────┬─────────────┘
       │                                                     │
       │            WebRTC（P2P 音视频）                     │
       │◄═══════════════════════════════════════════════════►│
       │                                                     │
┌──────┴───────┐        HTTPS / WebSocket        ┌───────────┴──────────────┐
│ 另一个浏览器  │ ◄─────────────────────────────► │ coturn（STUN/TURN）        │
└──────────────┘                                └──────────────────────────┘
```

- `server.js`：HTTP API、房间信令、聊天、在线状态、来电转发
- `lib/store.js`：SQLite schema、密码哈希、会话、联系人业务规则
- `public/`：原生 HTML/CSS/JS，无前端框架、无 CDN 依赖
- SQLite：用户、会话、联系人；数据文件默认在 `data/webrtc-chat.db`

## 目录结构

```
webrtc-chat/
├── server.js              # 信令服务器 + HTTP API + 静态文件
├── lib/store.js           # SQLite 账号/会话/联系人
├── public/                # Web 客户端（index.html / styles.css / app.js）
├── test/store.test.js     # SQLite 账号与联系人规则测试
├── test/server.test.js    # API/WebSocket/信令集成测试
├── scripts/e2e.js         # Playwright 双浏览器端到端测试
├── deploy/install.sh      # Ubuntu 安装/更新（systemd + TLS + SQLite 数据目录）
└── docs/                  # 部署记录与截图
```

## 本地运行

需要 **Node.js 22.5+**（内置 `node:sqlite`；推荐 22.22）。

```bash
cd webrtc-chat
npm install
npm start
```

打开 <http://localhost:8443>。本机 `localhost` 属于安全上下文，可以直接授权摄像头/麦克风；局域网其他设备必须使用 HTTPS。

数据库默认写到 `data/webrtc-chat.db`；删除该文件（以及 `-wal`/`-shm`）即可重置全部账号。

用两个浏览器窗口分别注册“手机用户”和“设备用户”：

1. 设备用户打开“设备用户 → 注册”，填写设备名和密码，记下分配的 12 位设备号；
2. 手机用户打开“手机用户 → 注册”，填写手机号、昵称、密码；
3. 手机用户在“添加联系人”输入设备号，发送请求；
4. 设备用户在“待我确认”点击“确认”；
5. 手机用户通讯录出现该设备后点击“通话”，设备接听即可看到双向画面和声音。

## 部署到局域网 / 公网

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

`/etc/turnserver.conf` 最小示例：

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
```

同一局域网通常可直连；跨网段、严格 NAT 或企业网络下需要 TURN。**如果 UDP 端口不好映射，至少转发 TCP 3478**，客户端会使用 `?transport=tcp` 的 TURN 中继（已验证可用）。

### 3. 安装本应用

```bash
sudo DOMAIN="www.shzhihuan.cn" LAN_IP="192.168.1.110" \
     STUN_URLS="stun:{host}:3478" \
     TURN_URLS="turn:{host}:3478?transport=udp,turn:{host}:3478?transport=tcp" \
     TURN_USER="webrtc" TURN_PASSWORD="CHANGE_ME" \
     ./deploy/install.sh
```

脚本会：

1. 复制应用（含 `lib/`）与生产依赖到 `/opt/webrtc-chat`
2. 创建 SQLite 数据目录 `/var/lib/webrtc-chat`（属主为运行用户），并写入 `DB_PATH`
3. 写入 `/etc/webrtc-chat/webrtc-chat.env`（权限 600）
4. 生成包含 `LAN_IP`、`DOMAIN`（以及 www 裸域）SAN 的自签名证书
5. 注册并启动 `webrtc-chat.service`（开机自启、失败重启）

访问 `https://<LAN_IP>:8443` 或 `https://<域名>:8443`，首次需要信任自签名证书。

常用运维：

```bash
sudo systemctl status webrtc-chat
sudo journalctl -u webrtc-chat -f
sudo systemctl restart webrtc-chat
```

### 4. 数据库备份 / 重置

```bash
# 备份（建议短暂停服，确保 WAL 已合并）
sudo systemctl stop webrtc-chat
sudo cp /var/lib/webrtc-chat/webrtc-chat.db /root/webrtc-chat-$(date +%F).db
sudo systemctl start webrtc-chat

# 重置全部账号（不可恢复）
sudo systemctl stop webrtc-chat
sudo rm -f /var/lib/webrtc-chat/webrtc-chat.db*
sudo systemctl start webrtc-chat
```

### 5. 域名与公网访问

1. 域名 A 记录指向该网络当前公网出口 IP；
2. 路由器把 **TCP 8443** 转发到运行本服务的机器；
3. 需要 TURN 中继时至少转发 **TCP 3478**（UDP 可选）；
4. 证书 SAN 包含域名（安装脚本的 `DOMAIN` 参数会处理）。

只在本局域网使用域名时，也可以把域名解析（路由器本地 DNS 或 hosts）指向服务器内网 IP。

> 本应用没有短信验证码、密码找回或管理员后台；暴露到公网前请评估风险并增加访问控制。

## 配置项

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `8443` | HTTPS 端口 |
| `HTTP_PORT` | `8080` | HTTP 跳转端口；设为空字符串关闭 |
| `TLS_CERT` / `TLS_KEY` | `certs/cert.pem`、`certs/key.pem` | 证书路径；不存在时退化为 HTTP |
| `DB_PATH` | `data/webrtc-chat.db` | SQLite 文件路径 |
| `STUN_URLS` | 空 | 逗号分隔 STUN 地址，支持 `{host}` 占位符 |
| `TURN_URLS` | 空 | 逗号分隔 TURN 地址，支持 `{host}` |
| `TURN_USER` / `TURN_PASSWORD` | 空 | coturn 长期凭据 |
| `MAX_PEERS` | `8` | 单房间人数上限 |

`{host}` 会被替换成浏览器访问页面时使用的主机名：用 IP 打开就下发 IP 版 TURN，用域名打开就下发域名版 TURN。

### HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/auth/register/phone` | 手机号注册 `{phone,name,password}` |
| POST | `/api/auth/register/device` | 设备注册 `{name,password}`，返回 12 位设备号 |
| POST | `/api/auth/login` | `{identifier,password}`，identifier 为手机号或设备号 |
| POST | `/api/auth/logout` | 注销当前 token |
| GET | `/api/me` | 当前用户 |
| GET | `/api/contacts` | 通讯录、待确认、已发出 |
| POST | `/api/contacts/request` | 手机传 `{deviceNumber}`，设备传 `{phone}` |
| POST | `/api/contacts/:id/confirm` | 确认请求 |
| POST | `/api/contacts/:id/reject` | 拒绝请求 |
| DELETE | `/api/contacts/:id` | 取消请求 / 删除联系人 |
| GET | `/api/ice` | 按请求主机名下发的 ICE 配置 |
| GET | `/healthz` | 房间、在线、用户、联系人统计 |

除注册/登录/ICE/healthz 外，HTTP API 需要 `Authorization: Bearer <token>`。

## 测试

```bash
# SQLite 账号/联系人规则 + HTTP/WebSocket 集成测试
npm test

# 双浏览器端到端：注册手机 + 注册设备 + 添加确认 + 通话 + 聊天 + 横竖屏
BASE_URL=http://localhost:8443 npm run e2e

# 已部署的 HTTPS 实例
BASE_URL=https://192.168.1.110:8443 npm run e2e

# 强制 TURN 中继（需配置 TURN）
BASE_URL=https://192.168.1.110:8443 npm run e2e -- --relay
```

E2E 使用虚拟摄像头/麦克风，验证：设备号格式、双向确认、双方 `RTCPeerConnection` `connected`、远端音视频轨道、双向聊天、麦克风开关、手机横屏和 Pad 竖屏无横向溢出，并输出截图到 `docs/screenshots/`。

## 响应式布局

- 宽屏（>900px）：通讯录侧栏 + 通话区 + 聊天侧栏（Pad 横屏、桌面）
- 窄屏（≤900px）：通讯录变为抽屉，通话区占满；聊天在竖屏下位于下方，横屏下位于右侧
- 已验证分辨率：1440×900、1024×768（Pad 横屏）、768×1024（Pad 竖屏）、844×390（手机横屏）、390×844（手机竖屏）
- 触控目标 ≥ 40px，输入框 ≥ 36px

## 安全与限制

- **没有短信验证码**：手机号注册采用“手机号 + 密码”方式，不校验号码归属；账号安全依赖密码。
- 没有密码找回、邮箱验证、管理员后台或封禁功能；忘记密码只能由运维直接改库。
- 密码使用 `scrypt` + 随机盐哈希；登录 token 只在数据库保存 SHA-256 摘要，有效期 30 天。
- 联系人关系是访问控制边界；但服务端仍允许匿名 WebSocket 加入随机房间（用于兼容性/测试），通话室 ID 为随机 UUID。若需严格隔离，可在此基础上加房间成员校验。
- 面向可信局域网或受控内网；公网部署请增加认证网关、速率限制和受信任证书。
- 自签名证书需要浏览器手动信任一次。
- SQLite 为单机存储，不支持多实例水平扩展；联系人/消息不持久化聊天记录。
- 没有通话记录、录制、屏幕共享、推送通知或群组通话（当前为 1 对 1 通话）。

## 故障排查

| 现象 | 排查 |
| --- | --- |
| 页面打不开 | `systemctl status webrtc-chat`；确认 8443 端口未被防火墙拦截 |
| 注册/登录 500 | 检查 `DB_PATH` 目录权限与磁盘空间；`journalctl -u webrtc-chat` |
| 无法授权摄像头 | 必须用 `https://` 或 `localhost`；确认已接受自签名证书 |
| 有聊天无画面 | 确认双方联系人已确认；检查 `RTCPeerConnection` 状态与 TURN 可达性 |
| 跨网段连不上 | 用 `npm run e2e -- --relay` 验证 TURN；检查 TCP/UDP 3478 与中继端口段 |
| 设备号不见了 | 设备号只在注册成功页展示；可让手机用户从已添加的联系人中查看，或用设备号+密码重新登录 |

## 当前部署

演示部署在 `192.168.1.110`（Ubuntu 22.04 虚拟机）：

- 客户端：<https://www.shzhihuan.cn:8443>（公网 DNS/NAT 待修正），局域网回退 <https://192.168.1.110:8443>
- 信令服务：`webrtc-chat.service`（systemd，开机自启）
- 数据库：`/var/lib/webrtc-chat/webrtc-chat.db`
- TURN：`turn:{host}:3478`，复用该机 coturn；凭据只保存在服务器环境文件
- 证书：自签名，SAN 含 `www.shzhihuan.cn`、`shzhihuan.cn`、`192.168.1.110`

详见 [`docs/deployment.md`](docs/deployment.md)。
