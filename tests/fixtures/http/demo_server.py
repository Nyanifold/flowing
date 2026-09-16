"""自研 demo HTTP server（本地回环）——RequestTool 测试服务端。

测试边界：RequestTool 测试一律打向本 server（``127.0.0.1`` 回环，
不起真实外网）。端点设计围绕清单 49 的断言点：URL 路径参数排除、
method 决定 body/query、auth 优先于 headers、expected_status 之外的
状态码、output schema 字段提取。

路由：

- ``POST /recommend/<category>`` → 200，回显 ``category`` / ``query`` /
  ``body`` / ``authorization`` / ``x-api-key``，另带无关字段 ``extra``
  （供 output 提取的「无关字段忽略」断言）；
- ``GET /search`` → 200，回显 ``query``；
- ``POST /always-conflict`` → 409（expected_status 之外的状态码负例）。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse


class _Handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _echo(self) -> dict:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {"_raw": raw.decode("utf-8", errors="replace")}
        return {
            "method": self.command,
            "path": parsed.path,
            "query": {k: v[0] if len(v) == 1 else v
                      for k, v in parse_qs(parsed.query).items()},
            "body": body,
            "authorization": self.headers.get("Authorization"),
            "x-api-key": self.headers.get("X-API-Key"),
            "extra": "noise",   # output schema 之外的无关字段
        }

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/always-conflict":
            self._reply(409, {"detail": "boom"})
            return
        if parsed.path.startswith("/recommend/"):
            payload = self._echo()
            payload["category"] = unquote(parsed.path.removeprefix("/recommend/"))
            self._reply(200, payload)
            return
        self._reply(404, {"detail": "not found"})

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/search":
            self._reply(200, self._echo())
            return
        self._reply(404, {"detail": "not found"})

    def log_message(self, *args) -> None:   # 静默（测试输出干净）
        pass


def create_server() -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    """启动回环 server（后台线程），返回 ``(server, thread, base_url)``。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}"


if __name__ == "__main__":   # 手动冒烟：python demo_server.py 后 Ctrl-C
    _server, _thread, url = create_server()
    print(f"demo http server @ {url}")
    _thread.join()
