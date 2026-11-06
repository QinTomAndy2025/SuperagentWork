# 部署记录：192.168.1.110

本文件记录仓库演示实例的实际部署方式，方便后续维护。敏感配置（TURN 密码等）只保存在服务器上，不写入仓库。

## 主机

| 项目 | 值 |
| --- | --- |
| 地址 | `192.168.1.110` |
| 系统 | Ubuntu 22.04（VirtualBox 虚拟机） |
| 运行用户 | `qinyj-vubuntu` |
| Node.js | v22.22.0，解压到 `/opt/node`，软链到 `/usr/local/bin` |
| 应用目录 | `/opt/webrtc-chat` |
| 配置 | `/etc/webrtc-chat/webrtc-chat.env`（600） |
| TLS | `/etc/webrtc-chat/tls/{cert,key}.pem`，自签名，SAN 含 IP |
| systemd | `webrtc-chat.service` |

## 部署步骤

```bash
# 在仓库根目录执行
sudo TURN_URLS="turn:192.168.1.110:3478?transport=udp,turn:192.168.1.110:3478?transport=tcp" \
     TURN_USER="webrtc" TURN_PASSWORD="***" \
     LAN_IP="192.168.1.110" \
     ./webrtc-chat/deploy/install.sh
```

## TURN / coturn

coturn 由本机先前的部署提供，systemd 服务名 `coturn`：

- `turn:192.168.1.110:3478`（UDP/TCP）
- `turns:192.168.1.110:5349`（TLS，自签证书；浏览器端不默认启用）
- 长期凭据用户 `webrtc`，密码保存在 `/etc/webrtc-chat/webrtc-chat.env`

服务端 `STUN_URLS` 与 `TURN_URLS` 指向上述地址，`/api/ice` 下发。

## 验证

```bash
curl -k https://192.168.1.110:8443/healthz
BASE_URL=https://192.168.1.110:8443 npm run e2e
BASE_URL=https://192.168.1.110:8443 npm run e2e -- --relay
```

结果见仓库 PR 描述中的截图与端到端输出。

## 验证记录（2026-11-06）

部署后从 WSL 客户端使用 Playwright（Chromium 158，虚拟摄像头/麦克风）对 `https://192.168.1.110:8443` 做端到端验证：

| 检查项 | 结果 |
| --- | --- |
| `GET /healthz` | `{"ok":true,"rooms":0,"peers":0,...}` |
| HTTPS / WSS | 正常（自签名证书，自签 SAN=`192.168.1.110`） |
| 双端加入同一房间 | 双方 `RTCPeerConnection.connectionState = connected` |
| 远端媒体 | 双方均收到 1 路音频 + 1 路视频，`videoWidth > 0` |
| 双向文字聊天 | 两个方向均收到消息 |
| 麦克风/摄像头开关 | 状态切换正常，媒体连接不中断 |
| 强制 TURN 中继（`--relay`） | 双方选定候选均为 `relay`（TURN UDP），音视频正常 |
| systemd | `webrtc-chat.service` active/enabled，重启后自动恢复 |
| 配置权限 | `/etc/webrtc-chat/webrtc-chat.env` 为 600（root），TLS 私钥 600 |

截图：

- `docs/screenshots/01-join-screen.png`：加入房间页
- `docs/screenshots/02-call-and-chat.png`：Alice 视角，双人通话 + 双向聊天
- `docs/screenshots/03-call-and-chat-other-side.png`：Bob 视角

复现命令：

```bash
cd webrtc-chat
BASE_URL=https://192.168.1.110:8443 npm run e2e
BASE_URL=https://192.168.1.110:8443 npm run e2e -- --relay
```

## 注意事项

- 自签名证书需要浏览器手动信任一次；换成受信任证书可消除提示。
- 没有账号/房间密码，适合可信局域网；不要直接暴露到公网。
- TURN 密码只存在于服务器环境文件，仓库与 PR 中不包含。
