"""面板 HTTP 服务(仅绑定 127.0.0.1,只读)。"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from ttxt import config as cfg
from ttxt.crawler import store

from . import api

WEB_DIR = cfg.DASHBOARD_DIR / "web"
MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
}


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "TouTiaoDashboard/0.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A003 - 标准库签名
        pass

    def _send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status=status)

    def _send_file(self, path: Path) -> None:
        try:
            content = path.read_bytes()
        except OSError:
            self._send_error_json(404, "not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", MIME_TYPES.get(path.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    @staticmethod
    def _int(query, key, default: int) -> int:
        try:
            return int((query.get(key) or [default])[0])
        except (TypeError, ValueError):
            return default

    def do_GET(self) -> None:  # noqa: N802 - 标准库方法名
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self._send_file(WEB_DIR / "index.html")
            return
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            if ".." in rel or rel.startswith("/"):
                self._send_error_json(400, "invalid path")
                return
            self._send_file(WEB_DIR / rel)
            return

        try:
            if path == "/api/overview":
                self._send_json(api.overview())
            elif path == "/api/categories":
                self._send_json(api.categories())
            elif path == "/api/jobs":
                self._send_json(api.jobs(self._int(query, "limit", 20)))
            elif path == "/api/logs":
                self._send_json(api.logs(self._int(query, "limit", 100)))
            elif path == "/api/model":
                self._send_json(api.model_status())
            elif path.startswith("/api/category/"):
                parts = [part for part in path[len("/api/category/"):].split("/") if part]
                if not parts or not store.SLUG_RE.match(parts[0]):
                    self._send_error_json(400, "invalid category")
                    return
                slug = parts[0]
                action = parts[1] if len(parts) > 1 else ""
                if action == "articles":
                    self._send_json(
                        api.articles(
                            slug,
                            self._int(query, "limit", 20),
                            self._int(query, "offset", 0),
                            (query.get("q") or [""])[0],
                        )
                    )
                elif action == "keywords":
                    self._send_json(api.keywords(slug))
                elif action == "analysis":
                    self._send_json(api.analysis(slug))
                else:
                    self._send_error_json(404, "not found")
            else:
                self._send_error_json(404, "not found")
        except Exception as exc:  # noqa: BLE001 - 面板需显示真实错误
            self._send_error_json(500, f"{type(exc).__name__}: {exc}")

    def do_POST(self) -> None:  # noqa: N802 - 只读面板
        self._send_error_json(405, "read-only dashboard")


def create_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), DashboardHandler)


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    httpd = create_server(host, port)
    actual_host, actual_port = httpd.server_address[:2]
    print(f"dashboard: http://{actual_host}:{actual_port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
