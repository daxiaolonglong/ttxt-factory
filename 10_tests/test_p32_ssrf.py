# -*- coding: utf-8 -*-
"""P32:出网 SSRF 安全边界回归测试。

覆盖四个出网入口(静态抓取 / 浏览器渲染 / robots / 跳转链),钉死这些语义:

1. 本机、私网、链路本地、组播、保留、IPv6 本地与 ULA、IPv4-mapped IPv6 一律拒绝;
2. 公网域名解析到私网 IP 时拒绝(防"域名伪装");
3. 跳转链**每一跳**都重新校验,而且**在校验通过前绝不对该跳发起连接**;
4. 静态抓取必须显式关闭库的自动跳转,禁止回退到库默认策略;
5. 浏览器请求拦截器对私网目标 abort、对公网目标 fallback(不破坏库自带的资源拦截);
   浏览器进程另带 ``--host-resolver-rules`` 作为第二层(Service Worker 等旁路);
6. robots.txt 同样走逐跳校验,且**生产入口 ``gate()``/``fetch_rules()`` 没有绕过参数**;
7. 本机服务(dashboard/model_gate)不属于出网路径,不接这套判定。
"""

from __future__ import annotations

import http.server
import inspect
import socket
import socketserver
import threading
from pathlib import Path

import pytest

from ttxt import config as cfg
from ttxt.crawler import fetcher, robots, url_safety
from ttxt.crawler.fetcher import FetchError
from ttxt.crawler.url_safety import UnsafeURLError

PROJECT = Path(__file__).resolve().parents[1]

BAD_IPS = [
    "127.0.0.1",
    "127.1.2.3",
    "::1",
    "0.0.0.0",
    "0.1.2.3",
    "10.0.0.1",
    "10.255.255.255",
    "172.16.0.1",
    "172.31.255.255",
    "192.168.9.9",
    "169.254.169.254",
    "224.0.0.5",
    "239.255.255.255",
    "240.0.0.1",
    "fd00::1",
    "fe80::1",
    "::ffff:127.0.0.1",
    "::ffff:169.254.169.254",
    "2002:7f00:1::",
]
GOOD_IPS = ["8.8.8.8", "1.1.1.1", "93.184.216.34", "172.15.0.1", "172.32.0.1", "100.127.0.1", "2001:4860:4860::8888"]

BAD_URLS = [
    "http://127.0.0.1:8080/x",
    "http://localhost/x",
    "http://LOCALHOST./x",
    "http://[::1]/x",
    "http://169.254.169.254/latest/meta-data/",
    "http://0.0.0.0/",
    "http://10.1.2.3/",
    "http://user:pass@127.0.0.1/",
    "file:///etc/passwd",
    "ftp://127.0.0.1/",
    "http:///no-host",
    "not-a-url",
]


class FakeResolver:
    """把域名映射到指定 IP,模拟"公网域名解析到内网"。"""

    def __init__(self, mapping: dict[str, list[str]], *, fail: set[str] | None = None) -> None:
        self.mapping = mapping
        self.fail = fail or set()
        self.calls: list[str] = []

    def __call__(self, host: str) -> list[str]:
        self.calls.append(host)
        if host in self.fail:
            raise socket.gaierror(11001, f"no such host: {host}")
        return list(self.mapping.get(host, ["93.184.216.34"]))


class FakePage:
    def __init__(self, status: int, headers: dict[str, str] | None = None, body: bytes = b"ok") -> None:
        self.status = status
        self.headers = headers or {}
        self.body = body


class FakeRoute:
    def __init__(self) -> None:
        self.aborted: list[str] = []
        self.fallback_called = 0
        self.continued = 0

    def abort(self, reason: str = "") -> None:
        self.aborted.append(reason or "")

    def fallback(self) -> None:
        self.fallback_called += 1

    def continue_(self) -> None:
        self.continued += 1


class FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url


class FakeContext:
    def __init__(self) -> None:
        self.listeners: dict[str, list[object]] = {}

    def on(self, event: str, handler) -> None:  # noqa: ANN001
        self.listeners.setdefault(event, []).append(handler)


class FakeWorker:
    def __init__(self) -> None:
        self.stopped = 0
        self.script_url = "https://example.com/sw.js"

    def stop(self) -> None:
        self.stopped += 1


class FakePageObject:
    """只记录 route 注册的假 Playwright page。"""

    def __init__(self, context: FakeContext | None = None) -> None:
        self.handlers: list[tuple[str, object]] = []
        self.context = context or FakeContext()

    def route(self, pattern: str, handler) -> None:
        self.handlers.append((pattern, handler))


# ---------------------------------------------------------------- 一、IP 判定


@pytest.mark.parametrize("ip", BAD_IPS)
def test_private_and_reserved_ips_are_blocked(ip: str) -> None:
    assert url_safety.is_blocked_ip(ip) is True, f"{ip} 必须被判定为禁止地址"


@pytest.mark.parametrize("ip", GOOD_IPS)
def test_public_ips_are_allowed(ip: str) -> None:
    assert url_safety.is_blocked_ip(ip) is False, f"{ip} 是公网地址,不得误杀"


# ---------------------------------------------------------------- 二、URL 判定


@pytest.mark.parametrize("url", BAD_URLS)
def test_check_url_rejects_dangerous_targets(url: str) -> None:
    verdict = url_safety.check_url(url)
    assert verdict.allowed is False, f"{url} 必须被拒绝"
    assert verdict.reason


def test_check_url_accepts_public_hostname() -> None:
    verdict = url_safety.check_url("https://example.com/a/b", resolver=FakeResolver({"example.com": ["93.184.216.34"]}))
    assert verdict.allowed is True, verdict.reason
    assert verdict.ips == ("93.184.216.34",)


@pytest.mark.parametrize("target", ["127.0.0.1", "169.254.169.254", "10.0.0.1", "fd00::1"])
def test_public_hostname_resolving_to_private_ip_is_rejected(target: str) -> None:
    resolver = FakeResolver({"news-spoof.example.com": [target]})
    verdict = url_safety.check_url("http://news-spoof.example.com/robots.txt", resolver=resolver)
    assert verdict.allowed is False
    assert target in verdict.reason
    assert resolver.calls == ["news-spoof.example.com"]


def test_multi_answer_dns_rejected_if_any_answer_is_private() -> None:
    resolver = FakeResolver({"dual.example.com": ["93.184.216.34", "127.0.0.1"]})
    verdict = url_safety.check_url("https://dual.example.com/", resolver=resolver)
    assert verdict.allowed is False
    assert "127.0.0.1" in verdict.reason


def test_dns_failure_is_not_treated_as_safe() -> None:
    resolver = FakeResolver({}, fail={"broken.example.com"})
    verdict = url_safety.check_url("https://broken.example.com/", resolver=resolver)
    assert verdict.allowed is False
    assert verdict.dns_failed is True


# ---------------------------------------------------------- 三、逐跳跳转校验


def _transport(script: dict[str, tuple[int, dict[str, str]]], calls: list[str]):
    def fetch_hop(url: str) -> url_safety.HopResponse:
        calls.append(url)
        status, headers = script.get(url, (200, {}))
        return url_safety.HopResponse(status=status, headers=headers, payload=url)

    return fetch_hop


def test_walk_follows_public_chain_and_records_every_hop() -> None:
    calls: list[str] = []
    script = {
        "https://a.example/1": (302, {"location": "https://b.example/2"}),
        "https://b.example/2": (301, {"location": "/3"}),
        "https://b.example/3": (200, {}),
    }
    result = url_safety.walk(
        "https://a.example/1",
        fetch_hop=_transport(script, calls),
        resolver=FakeResolver({"a.example": ["93.184.216.34"], "b.example": ["104.20.1.1"]}),
    )
    assert calls == ["https://a.example/1", "https://b.example/2", "https://b.example/3"]
    assert result.url == "https://b.example/3"
    assert [hop.status for hop in result.hops] == [302, 301, 200]
    assert result.hop.status == 200


def test_walk_rejects_private_hop_without_ever_connecting_to_it() -> None:
    calls: list[str] = []
    script = {"https://a.example/1": (302, {"location": "http://127.0.0.1:8765/admin"})}
    with pytest.raises(UnsafeURLError) as caught:
        url_safety.walk(
            "https://a.example/1",
            fetch_hop=_transport(script, calls),
            resolver=FakeResolver({"a.example": ["93.184.216.34"]}),
        )
    assert calls == ["https://a.example/1"], "私网那一跳必须在建立连接之前就被拒绝"
    assert "127.0.0.1" in caught.value.reason
    assert caught.value.url == "http://127.0.0.1:8765/admin"
    assert len(caught.value.hops) == 1


def test_walk_rejects_hop_whose_hostname_resolves_to_private() -> None:
    calls: list[str] = []
    script = {"https://a.example/1": (302, {"location": "https://evil.example/x"})}
    resolver = FakeResolver({"a.example": ["93.184.216.34"], "evil.example": ["192.168.10.10"]})
    with pytest.raises(UnsafeURLError) as caught:
        url_safety.walk("https://a.example/1", fetch_hop=_transport(script, calls), resolver=resolver)
    assert calls == ["https://a.example/1"]
    assert "192.168.10.10" in caught.value.reason


def test_walk_rejects_unsafe_initial_url_before_any_request() -> None:
    calls: list[str] = []
    with pytest.raises(UnsafeURLError):
        url_safety.walk("http://169.254.169.254/latest/meta-data/", fetch_hop=_transport({}, calls))
    assert calls == []


def test_walk_stops_on_redirect_loop() -> None:
    calls: list[str] = []
    script = {
        "https://a.example/1": (302, {"location": "https://a.example/2"}),
        "https://a.example/2": (302, {"location": "https://a.example/1"}),
    }
    with pytest.raises(UnsafeURLError) as caught:
        url_safety.walk(
            "https://a.example/1",
            fetch_hop=_transport(script, calls),
            max_hops=8,
            resolver=FakeResolver({"a.example": ["93.184.216.34"]}),
        )
    assert "重定向" in caught.value.reason


def test_walk_enforces_hop_budget() -> None:
    calls: list[str] = []
    script = {
        f"https://a.example/{index}": (302, {"location": f"https://a.example/{index + 1}"})
        for index in range(20)
    }
    with pytest.raises(UnsafeURLError) as caught:
        url_safety.walk(
            "https://a.example/0",
            fetch_hop=_transport(script, calls),
            max_hops=3,
            resolver=FakeResolver({"a.example": ["93.184.216.34"]}),
        )
    assert len(calls) == 4, "超出跳数预算必须提前停手"
    assert "跳" in caught.value.reason


def test_walk_treats_3xx_without_location_as_final() -> None:
    calls: list[str] = []
    script = {"https://a.example/1": (304, {})}
    result = url_safety.walk(
        "https://a.example/1",
        fetch_hop=_transport(script, calls),
        resolver=FakeResolver({"a.example": ["93.184.216.34"]}),
    )
    assert result.hop.status == 304
    assert len(calls) == 1


def test_walk_blocks_private_target_even_for_opt_in_when_flag_missing() -> None:
    calls: list[str] = []
    with pytest.raises(UnsafeURLError):
        url_safety.walk("http://127.0.0.1:9/x", fetch_hop=_transport({}, calls))
    assert calls == []


def test_walk_allow_private_flag_for_local_test_fixture_only() -> None:
    calls: list[str] = []
    script = {"http://127.0.0.1:9/x": (302, {"location": "http://127.0.0.1:9/y"}), "http://127.0.0.1:9/y": (200, {})}
    result = url_safety.walk("http://127.0.0.1:9/x", fetch_hop=_transport(script, calls), allow_private_ips=True)
    assert calls == ["http://127.0.0.1:9/x", "http://127.0.0.1:9/y"]
    assert result.hop.status == 200


# ------------------------------------------------------- 四、fetch_page 初始校验


def _bomb(*_args, **_kwargs):  # noqa: ANN001 - 只是"绝不该被调用"的探针
    raise AssertionError("安全校验失守:未通过校验就发起了请求")


def test_fetch_page_rejects_loopback_before_any_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetcher, "_fetch_once", _bomb)
    with pytest.raises(FetchError) as caught:
        fetcher.fetch_page("http://127.0.0.1:9/x")
    assert caught.value.terminal is True
    assert "SSRF" in str(caught.value)


def test_fetch_page_rejects_cloud_metadata_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetcher, "_fetch_once", _bomb)
    with pytest.raises(FetchError) as caught:
        fetcher.fetch_page("http://169.254.169.254/latest/meta-data/")
    assert caught.value.terminal is True


def test_fetch_page_rejects_bad_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetcher, "_fetch_once", _bomb)
    with pytest.raises(FetchError):
        fetcher.fetch_page("file:///etc/hosts")


def test_fetch_page_reports_dns_failure_as_retryable_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetcher, "_fetch_once", _bomb)
    monkeypatch.setattr(url_safety, "resolve_host", lambda host: (_ for _ in ()).throw(socket.gaierror(11001, "nx")))
    with pytest.raises(FetchError) as caught:
        fetcher.fetch_page("https://nope-does-not-exist.invalid/x", timeout=1.0)
    assert caught.value.terminal is False, "解析失败应当按网络故障重试,而不是当成安全拒绝"


# ------------------------------------------- 五、静态抓取必须自己管跳转


class FetcherSpy:
    def __init__(self, script: list[FakePage]) -> None:
        self.script = script
        self.kwargs: list[dict[str, object]] = []

    def get(self, url: str, **kwargs):  # noqa: ANN003
        self.kwargs.append({"url": url, **kwargs})
        return self.script.pop(0)


def test_static_path_disables_library_redirect_following(monkeypatch: pytest.MonkeyPatch) -> None:
    import scrapling.fetchers as fetchers

    spy = FetcherSpy([FakePage(302, {"location": "https://b.example/final"}), FakePage(200, {}, body=b"final")])
    monkeypatch.setattr(fetchers.Fetcher, "get", spy.get)
    monkeypatch.setattr(url_safety, "resolve_host", lambda host: ["93.184.216.34"])
    result = fetcher.fetch_page("https://a.example/start", timeout=5.0)
    assert all(entry["follow_redirects"] is False for entry in spy.kwargs), spy.kwargs
    assert [entry["url"] for entry in spy.kwargs] == ["https://a.example/start", "https://b.example/final"]
    assert result.status == 200
    assert result.url == "https://a.example/start", "FetchResult.url 语义不变(记录原始 URL)"


def test_static_path_refuses_to_degrade_when_option_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    import scrapling.fetchers as fetchers

    def broken_get(url: str, **kwargs):  # noqa: ANN003 - 模拟未来版本参数名变更
        raise TypeError("unexpected keyword argument 'follow_redirects'")

    monkeypatch.setattr(fetchers.Fetcher, "get", broken_get)
    monkeypatch.setattr(url_safety, "resolve_host", lambda host: ["93.184.216.34"])
    with pytest.raises(FetchError) as caught:
        fetcher.fetch_page("https://a.example/start", timeout=2.0)
    assert caught.value.terminal is True
    assert "follow_redirects" in str(caught.value)


def test_static_path_blocks_unsafe_redirect_hop(monkeypatch: pytest.MonkeyPatch) -> None:
    import scrapling.fetchers as fetchers

    spy = FetcherSpy([FakePage(302, {"location": "http://127.0.0.1:8765/pwned"})])
    monkeypatch.setattr(fetchers.Fetcher, "get", spy.get)
    monkeypatch.setattr(url_safety, "resolve_host", lambda host: ["93.184.216.34"])
    with pytest.raises(FetchError) as caught:
        fetcher.fetch_page("https://a.example/start", timeout=5.0)
    assert caught.value.terminal is True
    assert len(spy.kwargs) == 1, "私网跳转目标不得被真正请求"


# ------------------------------------------------------------- 六、浏览器侧


def test_browser_kwargs_carry_host_resolver_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    kwargs = fetcher._browser_kwargs()
    flags = kwargs.get("extra_flags") or []
    rule_flags = [flag for flag in flags if flag.startswith("--host-resolver-rules=")]
    assert rule_flags, f"浏览器必须带 host-resolver-rules 兜底:{flags}"
    rules = rule_flags[0]
    for needle in ("127.", "localhost", "10.", "192.168.", "172.16.", "169.254.", "~NOTFOUND"):
        assert needle in rules, f"host-resolver-rules 缺少 {needle}:{rules}"
    assert any(flag.startswith("--disk-cache-size=") for flag in flags), "磁盘缓存封顶旗标不得丢失"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("127.0.0.1:57777", "127.0.0.1"),
        ("http=127.0.0.1:57777;https=127.0.0.1:57778", "127.0.0.1"),
        ("socks5://Corp-Proxy:8080", "corp-proxy"),
        ("[::1]:8080", "::1"),
        ("<direct>", ""),
        ("", ""),
    ],
)
def test_split_proxy_host_parses_windows_proxy_forms(raw: str, expected: str) -> None:
    assert fetcher._split_proxy_host(raw) == expected


def test_host_resolver_rules_must_keep_the_loopback_proxy_resolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    """本机系统代理是回环地址(实测 127.0.0.1:57777):规则若打死代理主机,公网采集全断。

    Chromium 的 host-resolver-rules 先命中先生效,所以代理主机的恒等映射必须排在最前,
    其余私网字面量仍然判死。
    """
    monkeypatch.setattr(fetcher, "_browser_proxy_hosts", lambda: ("127.0.0.1",))
    rules = fetcher.ssrf_host_resolver_rules()
    assert rules.startswith("MAP 127.0.0.1 127.0.0.1,"), rules
    for needle in (
        "MAP 127.* ~NOTFOUND",
        "MAP localhost ~NOTFOUND",
        "MAP ::1 ~NOTFOUND",
        "MAP 10.* ~NOTFOUND",
        "MAP 192.168.* ~NOTFOUND",
        "MAP 169.254.* ~NOTFOUND",
    ):
        assert needle in rules, f"缺少 {needle}:{rules}"


def test_resolver_patterns_must_not_use_ipv6_brackets(monkeypatch: pytest.MonkeyPatch) -> None:
    """实测:规则串出现方括号(``[::1]`` 这种写法)会让 Chromium 判定语法非法并丢掉整串规则。

    所以模式表禁止方括号,并且一旦有人写错,构造规则串时必须直接报错,
    不允许带着"整串已失效"的配置继续启动浏览器。
    """
    for pattern in fetcher._PRIVATE_MAP_PATTERNS:
        assert "[" not in pattern and "]" not in pattern and "," not in pattern, f"模式非法:{pattern}"
    rules = fetcher.ssrf_host_resolver_rules()
    assert "MAP ::1 ~NOTFOUND" in rules, rules
    assert "[" not in rules, rules
    monkeypatch.setattr(fetcher, "_PRIVATE_MAP_PATTERNS", tuple(fetcher._PRIVATE_MAP_PATTERNS) + ("[::1]",))
    with pytest.raises(fetcher.FetchError):
        fetcher.ssrf_host_resolver_rules()


def test_host_resolver_rules_exempts_real_configured_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实环境(含 Windows 系统代理)下,代理主机不得被自己的规则拦掉。"""
    hosts = fetcher._browser_proxy_hosts()
    rules = fetcher.ssrf_host_resolver_rules()
    for host in hosts:
        assert rules.startswith(f"MAP {host} {host}"), f"代理主机 {host} 未被前置放行:{rules[:120]}"


def test_host_resolver_rules_comes_with_the_request_guard() -> None:
    kwargs = fetcher._browser_kwargs()
    assert kwargs["page_setup"] is fetcher._install_request_guard, "有第二层规则就必须有第一层拦截器"


def test_request_guard_aborts_private_and_falls_back_for_public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(url_safety, "resolve_host", lambda host: ["93.184.216.34"])
    page = FakePageObject()
    fetcher._install_request_guard(page)
    assert page.handlers, "拦截器必须注册 page.route"
    pattern, handler = page.handlers[0]
    for url in ("http://127.0.0.1:8765/admin", "http://localhost/x", "http://[::1]/x", "http://10.0.0.5/y", "ws://127.0.0.1:9/z"):
        route = FakeRoute()
        handler(route, FakeRequest(url))
        assert route.aborted, f"浏览器请求必须被拦截:{url}"
        assert route.fallback_called == 0
    for url in ("https://example.com/a.js", "https://cdn.example/img.png"):
        route = FakeRoute()
        handler(route, FakeRequest(url))
        assert route.aborted == []
        assert route.fallback_called == 1, "放行的请求要 fallback 回库自带的资源拦截,不得截断"


def test_request_guard_leaves_non_network_schemes_alone() -> None:
    page = FakePageObject()
    fetcher._install_request_guard(page)
    _pattern, handler = page.handlers[0]
    for url in ("about:blank", "blob:https://example.com/uuid", "data:text/html,hi"):
        route = FakeRoute()
        handler(route, FakeRequest(url))
        assert route.aborted == [], f"非网络协议不该由 IP 判定处理:{url}"


def test_request_guard_blocks_hostname_that_resolves_to_private(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(url_safety, "resolve_host", lambda host: ["10.10.10.10"])
    page = FakePageObject()
    fetcher._install_request_guard(page)
    _pattern, handler = page.handlers[0]
    route = FakeRoute()
    handler(route, FakeRequest("https://evil.example/x"))
    assert route.aborted


def test_guard_stops_service_worker_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    """SW 是 route 拦截的已知旁路:一旦出现必须立刻停掉(等效隔离)。"""
    monkeypatch.setattr(url_safety, "resolve_host", lambda host: ["93.184.216.34"])
    page = FakePageObject()
    fetcher._install_request_guard(page)
    listeners = page.context.listeners.get("serviceworker", [])
    assert listeners, "必须挂上 serviceworker 监听"
    worker = FakeWorker()
    for handler in listeners:
        handler(worker)
    assert worker.stopped == 1


def test_service_worker_hook_registered_once_per_context() -> None:
    context = FakeContext()
    fetcher._sw_hooked.clear()
    fetcher._install_request_guard(FakePageObject(context))
    fetcher._install_request_guard(FakePageObject(context))
    assert len(context.listeners["serviceworker"]) == 1, "同一 context 只挂一次,避免重复停 SW"
    fetcher._sw_hooked.clear()


# ---------------------------------------------------------------- 七、robots


class _Handler(http.server.BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, dict[str, str], bytes]] = {}
    hits: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        _Handler.hits.append(self.path)
        status, headers, body = _Handler.routes.get(self.path, (404, {}, b""))
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:  # 静默
        return


@pytest.fixture()
def local_server():
    _Handler.hits = []

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    httpd = Server(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_robots_blocks_private_target_without_opt_in(local_server: str) -> None:
    _Handler.routes = {"/robots.txt": (200, {}, b"User-agent: *\nDisallow: /secret\n")}
    rules = robots.fetch_robots(f"{local_server}/page", "ttxt-test/1.0", timeout=3)
    assert rules.unknown is True, "私网目标必须按「无法确认」处理,不得放行"
    assert rules.allowed("/anything") is False
    assert rules.blocked_reason, "必须留下拒绝原因"
    assert _Handler.hits == [], "被 SSRF 边界拒绝后不得再连本机服务"


def test_robots_follows_redirect_chain_per_hop_with_opt_in(local_server: str) -> None:
    port = local_server.rsplit(":", 1)[1]
    _Handler.routes = {
        "/robots.txt": (302, {"Location": "/real-robots"}, b""),
        "/real-robots": (200, {}, b"User-agent: *\nDisallow: /private/\n"),
    }
    rules = robots.fetch_robots(f"{local_server}/page", "ttxt-test/1.0", timeout=3, allow_private=True)
    assert rules.unknown is False, rules.blocked_reason
    assert _Handler.hits == ["/robots.txt", "/real-robots"], "必须逐跳自己取,而不是交给 urllib 自动跟"
    assert rules.allowed("/private/x") is False and rules.allowed("/public/x") is True


def test_robots_status_semantics_preserved_with_opt_in(local_server: str) -> None:
    _Handler.routes = {"/robots.txt": (500, {}, b"")}
    rules = robots.fetch_robots(f"{local_server}/page", "ttxt-test/1.0", timeout=3, allow_private=True)
    assert rules.unknown is True and rules.allowed("/x") is False
    _Handler.routes = {"/robots.txt": (404, {}, b"")}
    absent = robots.fetch_robots(f"{local_server}/page", "ttxt-test/1.0", timeout=3, allow_private=True)
    assert absent.unknown is False and absent.allowed("/x") is True


@pytest.mark.parametrize("entry", ["gate", "fetch_rules"])
def test_production_robots_entry_points_have_no_bypass(entry: str) -> None:
    signature = inspect.signature(getattr(robots, entry))
    assert "allow_private" not in signature.parameters
    assert "allow_private_ips" not in signature.parameters


def test_no_production_module_uses_the_private_opt_in() -> None:
    offenders: list[str] = []
    for path in (PROJECT / "ttxt").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "allow_private=True" in text or "allow_private_ips=True" in text:
            offenders.append(path.relative_to(PROJECT).as_posix())
    assert offenders == [], f"生产代码不得自行放行私网目标:{offenders}"


def test_local_services_are_not_routed_through_the_guard() -> None:
    """dashboard 与 model_gate 是本机服务,不属于出网路径;别把它们拦掉。"""
    for rel in ("ttxt/model_gate/client.py", "ttxt/doctor.py", "ttxt/dashboard/server.py"):
        path = PROJECT / rel
        if not path.exists():
            continue
        assert "url_safety" not in path.read_text(encoding="utf-8", errors="replace"), rel


# -------------------------------------------------------------- 八、DNS 缓存


def test_check_cached_reuses_resolution_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = FakeResolver({"cached.example.com": ["93.184.216.34"]})
    url_safety._DNS_CACHE.clear()
    monkeypatch.setattr(url_safety, "resolve_host", resolver)
    first = url_safety.check_url("https://cached.example.com/a", resolver=resolver, use_cache=True)
    second = url_safety.check_url("https://cached.example.com/b", resolver=resolver, use_cache=True)
    assert first.allowed and second.allowed
    assert resolver.calls == ["cached.example.com"], "TTL 内同一主机只解析一次"
    url_safety._DNS_CACHE.clear()
