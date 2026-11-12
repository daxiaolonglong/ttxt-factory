"""面板 HTTP 服务(仅绑定 127.0.0.1;GET 只读,POST 仅限任务下发接口)。"""

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
            elif path == "/api/sources":
                self._send_json(api.sources())
            elif path == "/api/tasks":
                self._send_json(api.task_queue(self._int(query, "limit", 20)))
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

    # ------------------------------------------------------------------
    # P11 任务下发(仅入队采集/本地分析)。安全护栏:
    # 1) 必须携带自定义头 X-TTXT-Task: 1 —— 跨站表单无法伪造,构成 CSRF 防线;
    # 2) Origin 存在时必须与本机 Host 完全一致 —— 拒绝任何第三方站点页面;
    # 3) Host 只允许回环地址 —— 阻断 DNS rebinding;
    # 4) 请求体上限 64KB,仅接受 JSON。

    _LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}

    def _task_allowed(self) -> str | None:
        if self.headers.get("X-TTXT-Task") != "1":
            return "缺少任务头 X-TTXT-Task: 1(防跨站请求)"
        host_header = self.headers.get("Host") or ""
        if host_header.startswith("["):
            host = host_header[1:].split("]", 1)[0]
        else:
            host = host_header.split(":")[0]
        if host not in self._LOOPBACK_HOSTS:
            return f"Host {host_header!r} 不是回环地址,拒绝"
        origin = self.headers.get("Origin")
        if origin is not None:
            expected = f"http://{host_header}" if host_header else None
            if expected is None or origin.rstrip("/") != expected.rstrip("/"):
                return f"Origin {origin!r} 与本机 {expected!r} 不一致,拒绝"
        return None

    def _read_json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > 64 * 1024:
            raise ValueError("请求体缺失或超过 64KB 上限")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("请求体必须是合法 JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def do_POST(self) -> None:  # noqa: N802 - 标准库方法名
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if not path.startswith("/api/tasks/"):
            self._send_error_json(405, "该面板仅接受任务下发接口 /api/tasks/*")
            return
        denied = self._task_allowed()
        if denied:
            self._send_error_json(403, denied)
            return
        try:
            body = self._read_json_body()
        except ValueError as exc:
            self._send_error_json(400, str(exc))
            return
        try:
            if path == "/api/tasks/crawl":
                payload = api.enqueue_crawl(str(body.get("source_id") or ""), body.get("category"))
                self._send_json(payload, status=202)
            elif path == "/api/tasks/analyze":
                self._send_json(api.run_analyze_task(str(body.get("category") or "")))
            else:
                self._send_error_json(
                    404, "未知任务接口;面板支持 crawl 入队与 analyze,写作/发布/模型不可下发"
                )
        except PermissionError as exc:
            self._send_error_json(403, str(exc))
        except ValueError as exc:
            self._send_error_json(400, str(exc))
        except Exception as exc:  # noqa: BLE001 - 面板需显示真实错误
            self._send_error_json(500, f"{type(exc).__name__}: {exc}")


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
