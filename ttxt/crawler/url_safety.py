# -*- coding: utf-8 -*-
"""P32:出网 SSRF 安全边界。所有对外部 URL 发起的网络请求都必须先过这里。

为什么单独一个模块:项目有四个出网入口(静态抓取、浏览器渲染、robots.txt、以及它们各自的
跳转链)。如果每个入口自己写一套判断,一定会写歪 —— 所以判定集中在这一处。

不可放宽的红线(规则 52):

1. 目标主机解析出的**每一个** IP 地址都必须是公网可路由地址;任一个落在本机/私网/链路本地/
   组播/保留段,整条 URL 拒绝(防止"一个答案公网、一个答案内网"的混合 DNS)。
2. 跳转链**每一跳**都要重新校验;第一跳安全不代表第二跳安全。校验不通过时**绝不建立连接**。
3. 浏览器路径除请求拦截器外,Chromium 进程还带 ``--host-resolver-rules`` 兜底,
   覆盖拦截器可能漏掉的旁路(例如 Service Worker 发起的请求)。
4. 允许"本机目标"的开关(``allow_private_ips`` / robots 的 ``allow_private``)只给测试夹具用;
   生产入口 ``fetch_page`` / ``robots.gate`` / ``robots.fetch_rules`` 不暴露它,
   并由 ``test_p32_ssrf.py`` 扫源码钉死。

已知残余风险(必须诚实):本机 Scrapling/curl_cffi 在本模块解析校验之后,连接时还会**自己再解析
一次**(curl_cffi 0.17 的 Session 不暴露 CURLOPT_RESOLVE,无法把连接目标钉死在校验过的 IP 上);
因此"解析后 IP 被换掉"(DNS rebinding)无法在这一层 100% 消除。当前缓解:每跳立即校验 + 浏览器层
由 Chromium 自己按 host-resolver-rules 解析拦截。这一窗口标记为**未完全缓解**。
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

#: 只允许这两个协议出网;file/ftp/gopher/data 等一律拒绝。
ALLOWED_SCHEMES = frozenset({"http", "https"})
#: 浏览器里 ws/wss 同样能连到内网端口,按 http/https 同等对待。
WEBSOCKET_SCHEMES = frozenset({"ws", "wss"})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
DEFAULT_MAX_HOPS = 5
#: 额外显式封掉的段(is_private 已覆盖大部分,这里补上容易被漏掉的)。
EXTRA_BLOCKED_V4 = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
)
EXTRA_BLOCKED_V6 = (ipaddress.ip_network("100::/64"), ipaddress.ip_network("2001:db8::/32"))

#: 主机名 → DNS 答案 的短 TTL 缓存;浏览器一个页面会发几十上百个子请求,
#: 每个都重新解析会把采集拖慢一个数量级。缓存里只存 DNS 答案,IP 判定每次都重做。
_DNS_CACHE: dict[str, tuple[float, tuple[str, ...]]] = {}
_DNS_LOCK = threading.Lock()


@dataclass(frozen=True)
class Verdict:
    """一个 URL 的安全判定结果。"""

    allowed: bool
    reason: str = ""
    host: str = ""
    ips: tuple[str, ...] = ()
    dns_failed: bool = False


@dataclass
class HopResponse:
    """传输层返回的一跳原始结果(``headers`` 的键必须已经小写)。"""

    status: int
    headers: dict[str, str] = field(default_factory=dict)
    payload: Any = None

    def header(self, name: str) -> str | None:
        return (self.headers or {}).get(name.lower())


@dataclass
class Hop:
    url: str
    status: int | None = None
    location: str | None = None
    ips: tuple[str, ...] = ()


@dataclass
class WalkResult:
    url: str
    hop: HopResponse
    hops: list[Hop] = field(default_factory=list)


class UnsafeURLError(RuntimeError):
    """URL 或其某一跳被安全边界拒绝。调用方必须按终态处理,不得重试去撞内网。"""

    def __init__(self, message: str, *, url: str, reason: str, hops: list[Hop] | None = None, dns_failed: bool = False) -> None:
        super().__init__(message)
        self.url = url
        self.reason = reason
        self.hops: list[Hop] = hops or []
        self.dns_failed = dns_failed


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """把"内嵌 IPv4 的 IPv6"还原成 v4,防止用 IPv6 写法绕过 v4 黑名单。

    手写位运算,不用 ``ipv4_mapped``/``sixto4``/``teredo_addr`` 里被标记废弃的属性。
    """
    if ip.packed[:12] == b"\x00" * 10 + b"\xff\xff":
        return ipaddress.IPv4Address(ip.packed[12:16])
    if int(ip) >> 112 == 0x2002:  # 6to4: 2002:VVVV:YYYY:...
        return ipaddress.IPv4Address(ip.packed[2:6])
    if ip.packed[:4] == b"\x20\x01\x00\x00" and ip.packed[4:12] == b"\x00" * 8:  # Teredo 2001::/32
        return ipaddress.IPv4Address(bytes(b ^ 0xFF for b in ip.packed[12:16]))
    return None


def _ip_is_bad(ip: ipaddress._BaseAddress) -> bool:
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return True
    if ip.version == 6:
        if getattr(ip, "is_site_local", False):
            return True
        if any(ip in network for network in EXTRA_BLOCKED_V6):
            return True
        embedded = _embedded_ipv4(ip)  # type: ignore[arg-type]
        return embedded is not None and _ip_is_bad(embedded)
    return any(ip in network for network in EXTRA_BLOCKED_V4)


def is_blocked_ip(value: Any) -> bool:
    """True 表示这个地址不得作为抓取目标(本机/私网/链路本地/组播/保留/内嵌 v4 的 IPv6)。"""
    try:
        ip = value if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address)) else ipaddress.ip_address(str(value).strip())
    except ValueError:
        return True  # 根本不是合法 IP 字面量:宁可拒,不可放
    return _ip_is_bad(ip)


def resolve_host(host: str) -> list[str]:
    """解析主机的全部 A/AAAA 记录(去重、稳定排序)。异常交给调用方判定为"无法确认"。"""
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    found: list[str] = []
    for info in infos:
        raw = str(info[4][0]).split("%", 1)[0]
        if raw not in found:
            found.append(raw)
    return sorted(found)


def _cached_lookup(host: str, resolver: Callable[[str], list[str]]) -> tuple[str, ...]:
    now = time.monotonic()
    with _DNS_LOCK:
        hit = _DNS_CACHE.get(host)
        if hit and hit[0] > now:
            return hit[1]
    addrs = tuple(resolver(host))
    with _DNS_LOCK:
        _DNS_CACHE[host] = (now + 60.0, addrs)
    return addrs


def check_url(
    url: str,
    *,
    resolver: Callable[[str], list[str]] | None = None,
    allow_private_ips: bool = False,
    use_cache: bool = False,
) -> Verdict:
    """判定一个 URL 是否可以出网。

    ``allow_private_ips`` 仅供**测试夹具**(本机 HTTP 服务)使用;生产入口不传。
    ``use_cache`` 让浏览器子请求复用同一主机的 DNS 答案(60 秒 TTL)。
    """
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        return Verdict(False, f"URL 无法解析:{exc}")
    scheme = (parts.scheme or "").lower()
    if scheme in WEBSOCKET_SCHEMES:
        scheme = "http"
    if scheme not in ALLOWED_SCHEMES:
        return Verdict(False, f"协议不允许:{parts.scheme or '(空)'}")
    if parts.username or parts.password:
        return Verdict(False, "URL 不得携带 user:pass@")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return Verdict(False, "URL 缺少主机名")
    if host == "localhost" or host.endswith(".localhost"):
        # 不能只靠 DNS:本机主机名在任何解析结果下都不允许成为抓取目标。
        return Verdict(False, f"本机主机名不得作为抓取目标:{host}", host=host)
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not allow_private_ips and _ip_is_bad(literal):
            return Verdict(False, f"字面 IP 属于禁止地址:{literal}", host=host, ips=(str(literal),))
        return Verdict(True, "", host=host, ips=(str(literal),))
    resolve = resolver or resolve_host
    try:
        addrs = _cached_lookup(host, resolve) if use_cache else tuple(resolve(host))
    except (OSError, socket.gaierror) as exc:
        return Verdict(False, f"域名解析失败:{exc}", host=host, dns_failed=True)
    if not addrs:
        return Verdict(False, "域名无解析结果", host=host, dns_failed=True)
    if not allow_private_ips:
        for addr in addrs:
            if is_blocked_ip(addr):
                return Verdict(False, f"{host} 解析到禁止的地址:{addr}", host=host, ips=tuple(addrs))
    return Verdict(True, "", host=host, ips=tuple(addrs))


def check_cached(url: str, *, ttl_note: str = "") -> Verdict:
    """浏览器请求拦截用的判定:同主机 DNS 走缓存,判定逻辑与 ``check_url`` 完全一致。"""
    return check_url(url, use_cache=True)


def walk(
    url: str,
    *,
    fetch_hop: Callable[[str], HopResponse],
    max_hops: int = DEFAULT_MAX_HOPS,
    resolver: Callable[[str], list[str]] | None = None,
    allow_private_ips: bool = False,
) -> WalkResult:
    """逐跳取一个 URL:每一跳先校验、再交给 ``fetch_hop`` 取,跳转由本项目自己走。

    传输层必须**关闭**自动跳转(``follow_redirects=False`` / urllib 的 no-redirect opener),
    否则本项目看不到中间跳,也就无法校验。
    """
    current = url
    chain: list[Hop] = []
    seen: dict[str, int] = {url: 1}
    for index in range(max_hops + 1):
        verdict = check_url(current, resolver=resolver, allow_private_ips=allow_private_ips)
        if not verdict.allowed:
            raise UnsafeURLError(
                f"目标被 SSRF 安全边界拒绝:{verdict.reason}",
                url=current,
                reason=verdict.reason,
                hops=chain,
                dns_failed=verdict.dns_failed,
            )
        hop = fetch_hop(current)
        status = int(hop.status or 0)
        location = hop.header("location") or hop.header("uri")
        chain.append(Hop(url=current, status=status, location=location, ips=verdict.ips))
        if status in REDIRECT_STATUSES and location:
            nxt = urljoin(current, location.strip())
            if seen.get(nxt, 0) >= 1:
                raise UnsafeURLError(f"重定向成环,已拒绝:{nxt}", url=nxt, reason=f"重定向成环:{nxt}", hops=chain)
            seen[nxt] = 1
            current = nxt
            continue
        return WalkResult(url=current, hop=hop, hops=chain)
    raise UnsafeURLError(
        f"跳转跳数超过上限 {max_hops},已停止",
        url=current,
        reason=f"跳转跳数超过上限 {max_hops}",
        hops=chain,
    )
