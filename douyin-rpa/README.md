# douyin-rpa

用 RPA（浏览器自动化）在**你自己的电脑**上发布抖音作品：上传视频/图文、填标题和文案/话题、点击发布。
登录只支持抖音 App **扫码**，脚本不接收手机号、密码或短信验证码，也不绕过验证码/风控。

> 这是浏览器自动化，不是抖音官方开放接口。请只发布自己的原创或已授权内容，遵守抖音用户协议和社区规范；
> 平台可能要求人工验证，脚本不会伪装或绕过这些验证。账号安全和使用后果由账号所有者负责。

## 工作方式

```text
douyin-rpa login      打开创作者中心 → 保存二维码 PNG → 用户用抖音 App 扫码
                      登录 Cookie 写入持久化 profile（默认 ~/.douyin-rpa/profile）
douyin-rpa publish    复用 profile → 打开发布页 → 上传媒体 → 填标题/文案 → 等待上传完成
                      默认停在预览；加 --publish 才点击「发布」
```

- **登录和短信验证码由账号本人完成**。脚本没有任何地方接收手机号、密码或验证码。
- 二维码约 2 分钟有效；等待扫码期间二维码刷新会覆盖同一张 PNG。
- 无头/远程机器上，把保存下来的二维码图片发到手机（抖音「扫一扫」支持从相册选图）。

## 安装

需要 Python 3.9+ 和 Google Chrome（推荐）。也可以只用 Playwright 自带的 Chromium：

```bash
cd douyin-rpa
python3 -m pip install -e .

# 不打算用系统 Chrome 时：
python3 -m playwright install chromium
```

验证安装：

```bash
douyin-rpa --version
douyin-rpa status      # 未登录时返回码为 2
```

## 登录（必须由账号本人扫码）

```bash
douyin-rpa login --qr-out ./login-qr.png
```

- 无头机器上不会弹窗，二维码会写到 `./login-qr.png`。
- 把 PNG 显示在手机或另一台设备上，用抖音 App「扫一扫」扫描；也可以在扫一扫里从相册选择这张图。
- 默认等待 300 秒（`--timeout` 可调）。二维码过期会自动刷新并覆盖同一个文件。
- 登录成功后 Cookie 保存在 `~/.douyin-rpa/profile`，后续发布直接复用。

检查会话是否还有效：

```bash
douyin-rpa status
# 或
douyin-rpa status --json
```

> 远程场景：让能操作这台机器的人把二维码图片发给你；不要把你的手机号、密码、短信验证码发给任何人。
> 二维码只在扫码登录这一次有效，不能当作长期凭据。

## 发布

视频：

```bash
douyin-rpa publish \
  --video ./media/clip.mp4 \
  --title "今天的作品" \
  --caption "记录生活" \
  --tags 日常 vlog
```

图文：

```bash
douyin-rpa publish \
  --images ./media/1.jpg ./media/2.png \
  --title "图文标题" \
  --caption "文案" \
  --publish
```

- **默认是预览**：只上传媒体并填好内容，不点「发布」，会保存一张填好的页面截图。
  确认无误后加 `--publish` 再执行一次。
- 上传大视频时可以用 `--timeout 1200` 延长等待。
- 成功、失败截图和失败时的 HTML 快照保存在 `~/.douyin-rpa/artifacts/<时间>/`。

## 内容清单

适合批量或由程序生成内容时使用：

```json
{
  "type": "video",
  "video": "media/clip.mp4",
  "title": "标题",
  "caption": "文案",
  "tags": ["日常", "vlog"]
}
```

```bash
douyin-rpa publish --manifest ./content.json --publish
```

图文把 `type` 改成 `images`，用 `images` 数组指定多张图片（最多 35 张）。
更多示例见 [`examples/`](examples/)。

约束（与当前创作者中心页面一致）：

| 项目 | 限制 |
| --- | --- |
| 标题 | 最多 30 字 |
| 作品描述（含话题） | 最多 1000 字 |
| 图文图片 | 1–35 张，jpg/jpeg/png/webp |
| 视频 | 1 个，mp4/mov/webm 等；超过 4 GiB 时网页端可能拒绝 |

## 选择器维护

抖音创作者中心的 DOM 会随版本变化。默认选择器是一组“候选”：文本、placeholder、无障碍角色优先，CSS 兜底。
发布失败时会保存 `failure.png` 和 `failure.html`，用浏览器 DevTools 找到新元素后，用一个 JSON 覆盖即可：

```json
{
  "publish_button": ["role:button:发布"],
  "caption_editor": ["css:.zone-container [contenteditable='true']"]
}
```

```bash
douyin-rpa publish --manifest ./content.json --selectors ./selectors.json --publish
```

支持的前缀：`css:`、`text:`（包含）、`text!:`（完全相等）、`placeholder:`、`role:<role>:<name>`。

## 无头 / 远程 / 自动化调用

- 默认无头（headless），不需要图形界面；`--show` 可以弹出窗口人工观察（需要桌面环境）。
- 登录二维码是唯一必须人工参与的步骤。登录一次后，发布可以完全由脚本完成。
- 失败时退出码：`0` 成功；`1` 一般错误（含内容/选择器/上传问题）；`2` 未登录。
- 建议 cron/定时任务先跑 `douyin-rpa status`，未登录时提醒用户重新扫码，而不是尝试自动登录。

## 设计边界

- 不接收、不保存、不填写账号密码或短信验证码。
- 不复用别人的 Cookie/会话；profile 只在本机。
- 不做验证码识别、风控绕过、批量刷屏或批量账号操作。
- 不做定时发布、合集、位置、封面裁剪等高级设置；封面使用页面默认帧。

## 测试

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest
```

测试使用本地假创作者中心页面，在真实 Chrome 里跑完整流程（登录检测、二维码保存、视频/图文上传、
标题/文案填写、确认弹窗、发布成功判断），不访问抖音、不使用任何真实账号。没有 Chrome 时浏览器测试会跳过。
