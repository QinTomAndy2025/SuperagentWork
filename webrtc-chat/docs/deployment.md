# 部署记录：192.168.1.110

本文件记录仓库演示实例的实际部署方式，方便后续维护。敏感配置（TURN 密码等）只保存在服务器上，不写入仓库。

## 主机与组件

| 项目 | 值 |
| --- | --- |
| 地址 | `192.168.1.110` |
| 域名 | `www.shzhihuan.cn`（端口 8443） |
| 系统 | Ubuntu 22.04（VirtualBox 虚拟机） |
| 运行用户 | `qinyj-vubuntu` |
| Node.js | v22.22.0，解压到 `/opt/node`，软链到 `/usr/local/bin` |
| 应用目录 | `/opt/webrtc-chat` |
| 数据库 | `/var/lib/webrtc-chat/webrtc-chat.db`（SQLite，WAL） |
| 配置 | `/etc/webrtc-chat/webrtc-chat.env`（600） |
| TLS | `/etc/webrtc-chat/tls/{cert,key}.pem`，自签名，SAN 含域名和 IP |
| systemd | `webrtc-chat.service` |
| TURN | coturn：`turn:{host}:3478`（UDP/TCP）；TLS 5349 保留 |

## 架构变更（账号 + 联系人）

服务端新增：

- `lib/store.js`：Node 内置 `node:sqlite`，表 `users` / `sessions` / `contacts`
- 手机号注册、设备注册（自动生成唯一 12 位设备号）、登录、30 天会话 token
- 联系人请求/确认/拒绝/删除；手机只能添加设备、设备只能添加手机；双向请求自动确认
- WebSocket 首个消息 `{type:"auth", token}` 建立登录态；在线状态、联系人事件、来电/接听/拒绝转发
- `/api/contacts` 返回在线状态；`/healthz` 增加 `users` / `contacts` 统计
- 客户端新增登录/注册、通讯录、来电弹窗，通话仍复用原有 WebRTC mesh + 聊天

数据库文件默认由安装脚本设为 `/var/lib/webrtc-chat/webrtc-chat.db`，目录属主为服务用户。

## 部署步骤

```bash
# 在仓库根目录执行
sudo DOMAIN="www.shzhihuan.cn" LAN_IP="192.168.1.110" \
     STUN_URLS="stun:{host}:3478" \
     TURN_URLS="turn:{host}:3478?transport=udp,turn:{host}:3478?transport=tcp" \
     TURN_USER="webrtc" TURN_PASSWORD="***" \
     ./webrtc-chat/deploy/install.sh
```

安装脚本会复制 `server.js`、`lib/`、`public/`、生产依赖，创建 `/var/lib/webrtc-chat`，在环境文件中补齐 `DB_PATH`（已有配置不会覆盖），生成证书并重启 systemd 服务。

## TURN / coturn

coturn 由本机先前的部署提供，systemd 服务名 `coturn`：

- `turn:{host}:3478`（UDP/TCP；`/api/ice` 把 `{host}` 替换为浏览器访问用的主机名，局域网 IP 和域名两种入口都可用）
- `turns:192.168.1.110:5349`（TLS，自签证书；浏览器端不默认启用）
- 长期凭据用户 `webrtc`，密码保存在 `/etc/webrtc-chat/webrtc-chat.env`

若 UDP 端口不好映射，只转发 **TCP 3478** 即可使用 TURN over TCP；已在部署实例验证 `relayProtocol=tcp`。

## 数据库备份 / 重置

```bash
sudo systemctl stop webrtc-chat
sudo cp /var/lib/webrtc-chat/webrtc-chat.db /root/webrtc-chat-$(date +%F).db
sudo systemctl start webrtc-chat

# 重置全部账号（不可恢复）
sudo systemctl stop webrtc-chat
sudo rm -f /var/lib/webrtc-chat/webrtc-chat.db*
sudo systemctl start webrtc-chat
```

## 验证记录（2026-11-06）

本地与部署实例使用 Playwright（Chromium 158，虚拟摄像头/麦克风）验证：

| 检查项 | 结果 |
| --- | --- |
| `npm test` | 15/15 通过（8 个 SQLite 规则测试 + 7 个 HTTP/WebSocket 集成测试） |
| 手机注册 / 登录 | 通过，手机号 + 密码，token 持久化 |
| 设备注册 | 通过，返回唯一 12 位设备号，自动激活，可重新登录 |
| 手机添加设备 | 通过，按 12 位设备号发起，设备端确认后双方通讯录各出现 1 个联系人 |
| 设备添加手机 | 通过，按手机号发起，手机端确认；双向请求自动确认的规则也有测试覆盖 |
| 未确认 / 未登录访问 | 401；非法角色号码组合 400；重复请求 409 |
| 通话 | 手机点击通话 → 设备来电弹窗 → 接听后双方 `connected`，各收到 1 路音频 + 1 路视频 |
| 通话聊天 | 双向送达 |
| 麦克风/摄像头开关 | 状态切换，不中断媒体 |
| 响应式 | 1024×768、768×1024、844×390、390×844 均无横向溢出；Pad 横竖屏和手机横屏截图见下 |
| TURN 中继 | `--relay` 通过，双方选定候选均为 `relay`，`relayProtocol=tcp` |
| systemd | active/enabled，重启后账号与联系人仍在 |

## 截图

| 文件 | 内容 |
| --- | --- |
| `01-auth-desktop.png` | 桌面登录/注册页 |
| `02-device-register.png` | Pad 横屏设备注册成功，显示 12 位设备号 |
| `03-contacts-tablet.png` | Pad 横屏通讯录（已确认联系人、在线状态） |
| `04-call-phone.png` | 手机视角桌面尺寸通话 + 聊天 |
| `05-call-device-tablet-landscape.png` | Pad 横屏通话 |
| `06-call-phone-landscape.png` | 手机横屏 844×390 通话 |
| `07-call-pad-portrait.png` | Pad 竖屏 768×1024 通话 |
| `before/` | 上一版本（账号功能之前）的 3 张界面截图，用于 PR 的 Before 对比 |

## 域名公网访问状态

服务端已按域名完成配置（证书 SAN、`{host}` 版 STUN/TURN），但公网可达性取决于 DNS 和端口映射：

- `www.shzhihuan.cn` 的 A 记录当前指向的公网 IP，与本网络的实际公网出口 IP 不一致；
- 该 A 记录地址上的 8443 端口当前拒绝连接，因此从公网访问 `https://www.shzhihuan.cn:8443` 还不能到达本服务；
- 需要在域名 DNS 中把 A 记录改为本网络当前公网 IP，并在路由器上把 TCP 8443 转发到 `192.168.1.110`；如需 TURN 中继，至少再转发 TCP 3478（UDP 可选）。

在 DNS/端口映射修正前，局域网内可直接访问 `https://192.168.1.110:8443`，或通过本地 DNS/hosts 把域名指向 `192.168.1.110`。

## 注意事项

- 没有短信验证码：手机号注册不校验号码归属，仅用密码保护账号。
- 没有密码找回/管理员后台；忘记密码只能直接改库或让用户重新注册。
- 自签名证书需要浏览器手动信任一次；公网建议换受信任证书。
- 联系人确认是访问控制边界；服务端仍保留匿名 WebSocket 加入房间的兼容路径（随机 UUID 通话室），严格隔离可在此之上增加房间成员校验。
- TURN 密码只存在于服务器环境文件，仓库与 PR 中不包含。
