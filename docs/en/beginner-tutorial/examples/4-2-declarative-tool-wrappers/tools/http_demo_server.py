"""Local demo HTTP server (the server side of the request tool demo): stdlib-only, loopback only.

Usage: python tools/http_demo_server.py [port]   # default 8641
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_STARTED = time.time()
_ORDERS: dict[str, dict] = {}


class Handler(BaseHTTPRequestHandler):
    def _send(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence access logs
        pass

    def do_GET(self):
        if self.path.startswith("/api/status"):
            payload = {"status": "ok", "uptime": round(time.time() - _STARTED, 1)}
            if "verbose=true" in self.path:
                payload["detail"] = {"orders": len(_ORDERS), "version": "1.7-demo"}
            self._send(payload)
        elif self.path.startswith("/api/orders/"):
            oid = self.path.rsplit("/", 1)[-1]
            if oid in _ORDERS:
                self._send(_ORDERS[oid])
            else:
                self._send({"error": "not found"}, 404)
        else:
            self._send({"error": "unknown path"}, 404)

    def do_POST(self):
        if self.path == "/api/orders":
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            oid = f"ord-{len(_ORDERS) + 1:03d}"
            _ORDERS[oid] = {"order_id": oid, "item": data.get("item"),
                            "count": data.get("count", 1), "total": data.get("count", 1) * 9.9}
            self._send(_ORDERS[oid], 201)
        else:
            self._send({"error": "unknown path"}, 404)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8641
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
