"""测试用的抖音创作者中心假页面。

用真实的浏览器跑完整发布流程，验证选择器、上传、填表、发布和成功判断；
不连接抖音，也不产生任何真实内容。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# 1x1 透明 PNG，页面用 CSS 放大成 178x178，模拟二维码图片。
TINY_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

LOGIN_HTML = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>抖音创作者中心</title></head>
<body>
  <div>抖音创作者中心</div>
  <div>扫码登录</div>
  <div>验证码登录</div>
  <img id="qr" alt="登录二维码" style="width:178px;height:178px"
       src="data:image/png;base64,{TINY_PNG}">
</body></html>
"""

HOME_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>创作者中心</title></head>
<body><div>创作者中心</div><button>发布作品</button></body></html>
"""

UPLOAD_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>发布作品</title>
<style>body { font-family: sans-serif; margin: 24px; }
.zone-container { border: 1px solid #ccc; min-height: 80px; padding: 8px; margin: 12px 0; }
#toast { color: green; font-weight: bold; }</style></head>
<body>
  <nav><span id="tab-video">发布视频</span> | <span id="tab-image">发布图文</span></nav>
  <h1>发布作品</h1>
  <label>作品标题 <input id="title" placeholder="填写作品标题"></label>
  <div class="zone-container">
    <div id="editor" contenteditable="true" data-placeholder="添加作品描述"></div>
  </div>
  <input id="file" type="file" accept="video/mp4">
  <div id="file-list"></div>
  <button id="publish" disabled>发布</button>
  <div id="toast"></div>
  <script>
    const confirmMode = __CONFIRM__;
    const file = document.getElementById('file');
    const publish = document.getElementById('publish');
    const editor = document.getElementById('editor');
    const title = document.getElementById('title');

    document.getElementById('tab-image').addEventListener('click', () => {
      file.accept = 'image/*';
      file.multiple = true;
      document.body.dataset.type = 'image';
    });

    file.addEventListener('change', () => {
      window.__files = Array.from(file.files).map(f => f.name);
      document.getElementById('file-list').textContent = window.__files.join(', ');
      setTimeout(() => { publish.disabled = false; }, 250);
    });

    function finish() {
      const payload = {
        title: title.value,
        caption: editor.innerText.trim(),
        files: window.__files || [],
        type: document.body.dataset.type || 'video'
      };
      window.__published = payload;
      const toast = document.getElementById('toast');
      toast.textContent = '发布成功';
      fetch('/__published', { method: 'POST', body: JSON.stringify(payload) });
    }

    publish.addEventListener('click', () => {
      if (confirmMode) {
        const dialog = document.createElement('div');
        dialog.setAttribute('role', 'dialog');
        dialog.appendChild(document.createTextNode('确认发布？'));
        const button = document.createElement('button');
        button.textContent = '确定';
        button.addEventListener('click', () => { dialog.remove(); finish(); });
        dialog.appendChild(button);
        document.body.appendChild(dialog);
        return;
      }
      finish();
    });
  </script>
</body></html>
"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "MockDouyin/0.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    @property
    def state(self) -> dict[str, Any]:
        return self.server.state  # type: ignore[attr-defined]

    def _logged_in(self) -> bool:
        return "mock_session=1" in (self.headers.get("Cookie") or "")

    def _send(self, status: int, body: str, content_type: str = "text/html; charset=utf-8") -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/":
            if self._logged_in():
                self._redirect("/creator-micro/home")
            else:
                body = LOGIN_HTML
                if self.state.get("auto_login_after_ms"):
                    delay = int(self.state["auto_login_after_ms"])
                    body = body.replace(
                        "</body>",
                        f"<script>setTimeout(() => {{ document.cookie='mock_session=1; path=/'; "
                        f"location.href='/creator-micro/home'; }}, {delay});</script></body>",
                    )
                self._send(200, body)
            return
        if path == "/creator-micro/home":
            if self._logged_in():
                self._send(200, HOME_HTML)
            else:
                self._redirect("/")
            return
        if path == "/creator-micro/content/upload":
            if not self._logged_in():
                self._redirect("/")
                return
            body = UPLOAD_HTML.replace("__CONFIRM__", "true" if self.state["confirm"] else "false")
            self._send(200, body)
            return
        self._send(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path != "/__published":
            self._send(404, "not found")
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw}
        self.state["published"].append(payload)
        self.send_response(204)
        self.end_headers()


class MockDouyinServer:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {"published": [], "confirm": False, "auto_login_after_ms": 0}
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.state = self.state  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def start(self) -> "MockDouyinServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def published(self) -> list[dict[str, Any]]:
        return self.state["published"]

    @property
    def confirm_mode(self) -> bool:
        return bool(self.state["confirm"])

    @confirm_mode.setter
    def confirm_mode(self, value: bool) -> None:
        self.state["confirm"] = value

    @property
    def auto_login_after_ms(self) -> int:
        return int(self.state.get("auto_login_after_ms") or 0)

    @auto_login_after_ms.setter
    def auto_login_after_ms(self, value: int) -> None:
        self.state["auto_login_after_ms"] = int(value)
