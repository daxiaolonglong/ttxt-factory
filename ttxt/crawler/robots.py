"""robots.txt 获取与解析。

安全原则:
- HTTP 200 → 按规则解析;
- 404/410 → 视为"无规则"(robots 缺席的标准语义是允许);
- 其他异常(5xx/网络错误/超时)→ `unknown=True`,调用方必须暂停该来源,不得放行。
解析范围:仅 `User-agent: *` 组;支持路径前缀、`*` 通配符与 `$` 行尾锚。

P32 SSRF:取 robots.txt 也是一次出网。初始 URL 与**每一跳**都过 ``url_safety``,
跳转由本项目自己走(``_NoRedirect`` 关掉 urllib 的自动跟随),否则中间跳看不见、无法校验。
被拒绝或解析失败一律 `unknown=True` + `blocked_reason`,由 `gate()` 挡在采集之前。
`allow_private` 只为测试夹具(本机 HTTP 服务)存在;`gate()`/`fetch_rules()` 不暴露它,
生产路径没有任何绕过入口。
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from ttxt.crawler import url_safety
from ttxt.logging import get_logger

LOG = get_logger("crawler")


@dataclass
class RobotsRules:
    allow_all: bool = True
    rules: list[tuple[bool, str]] = field(default_factory=list)  # (allow, pattern)
    crawl_delay_s: float | None = None
    unknown: bool = False
    blocked_reason: str = ""

    def allowed(self, path: str) -> bool:
        if self.unknown:
            return False
        if self.allow_all:
            return True
        best_len = -1
        verdict = True
        for allow, pattern in self.rules:
            if _pattern_matches(pattern, path) and len(pattern) > best_len:
                best_len = len(pattern)
                verdict = allow
        return verdict


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止 urllib 自己跟跳转:每一跳必须交回本项目逐跳校验。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001,D102
        return None


def _pattern_matches(pattern: str, path: str) -> bool:
    if not pattern:
        return False
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = re.escape(body).replace(r"\*", ".*")
    if anchored:
        return bool(re.fullmatch(regex, path))
    return bool(re.match(regex, path))


def parse_robots(text: str) -> RobotsRules:
    rules = RobotsRules(allow_all=False, unknown=False)
    active = False
    has_rules = False
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            active = value == "*"
        elif active and key == "disallow":
            has_rules = True
            rules.rules.append((False, value))
        elif active and key == "allow":
            has_rules = True
            rules.rules.append((True, value))
        elif active and key == "crawl-delay":
            try:
                rules.crawl_delay_s = float(value)
            except ValueError:
                pass
    if not has_rules:
        rules.allow_all = True
    return rules


def _hop_headers(message) -> dict[str, str]:  # noqa: ANN001 - http.client.HTTPMessage
    try:
        items = list(message.items())
    except AttributeError:
        return {}
    return {str(key).lower(): str(value) for key, value in items}


def _robots_hop_fetcher(opener, user_agent: str, timeout: float):  # noqa: ANN001
    def fetch_hop(target: str) -> url_safety.HopResponse:
        request = urllib.request.Request(target, headers={"User-Agent": user_agent})
        try:
            with opener.open(request, timeout=timeout) as response:
                return url_safety.HopResponse(
                    status=int(response.status),
                    headers=_hop_headers(response.headers),
                    payload=response.read(),
                )
        except urllib.error.HTTPError as exc:
            try:
                payload = exc.read()
            except Exception:  # noqa: BLE001 - 错误体读不出来不影响判定
                payload = b""
            return url_safety.HopResponse(status=int(exc.code), headers=_hop_headers(exc.headers), payload=payload)

    return fetch_hop


def fetch_robots(url: str, user_agent: str, timeout: float = 10, *, allow_private: bool = False) -> RobotsRules:
    """取并解析 robots.txt;初始 URL 与每一跳都先过 SSRF 判定。

    ``allow_private`` 仅供测试夹具(本机 127.0.0.1 HTTP 服务)使用。生产入口
    ``fetch_rules()``/``gate()`` 不转发该参数,``test_p32_ssrf.py`` 会扫源码钉死
    生产模块里不得出现把这个开关置真的写法。
    """
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return RobotsRules(allow_all=False, unknown=True, blocked_reason=f"URL 不可用:{url[:120]}")
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        result = url_safety.walk(
            robots_url,
            fetch_hop=_robots_hop_fetcher(opener, user_agent, timeout),
            max_hops=url_safety.DEFAULT_MAX_HOPS,
            allow_private_ips=allow_private,
        )
    except url_safety.UnsafeURLError as exc:
        LOG.warn("crawl.robots.blocked_by_ssrf_guard", url=exc.url[:200], reason=exc.reason, hops=len(exc.hops))
        return RobotsRules(allow_all=False, unknown=True, blocked_reason=exc.reason)
    except urllib.error.URLError as exc:
        return RobotsRules(allow_all=False, unknown=True, blocked_reason=f"网络失败:{exc}")
    except Exception as exc:  # noqa: BLE001 - 无法确认规则 → 暂停来源,不放行
        return RobotsRules(allow_all=False, unknown=True, blocked_reason=f"{type(exc).__name__}:{exc}")
    if len(result.hops) > 1:
        LOG.info(
            "crawl.robots.redirect_chain",
            url=robots_url,
            final=result.url,
            chain=" | ".join(f"{hop.status}->{hop.location or ''}" for hop in result.hops[:-1]),
        )
    status = int(result.hop.status or 0)
    if status == 200:
        return parse_robots(bytes(result.hop.payload or b"").decode("utf-8", errors="replace"))
    if status in (404, 410):
        return RobotsRules(allow_all=True)
    return RobotsRules(allow_all=False, unknown=True, blocked_reason=f"HTTP {status}")


def target_path(url: str) -> str:
    parts = urlsplit(url)
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def fetch_rules(url: str, user_agent: str, timeout: float = 10.0) -> RobotsRules:
    """统一取规则入口(host 级):同一 host 的 robots.txt 只取一次。"""
    return fetch_robots(url, user_agent, timeout=timeout)


def gate(url: str, user_agent: str, cache: dict[str, RobotsRules], timeout: float = 10.0) -> tuple[RobotsRules, bool]:
    """P12:带 host 级规则缓存的逐 URL 门禁,返回 (rules, 规则允许)。

    分页/列表跳转出的每个新 URL 都必须重新过闸;规则按 host 缓存避免逐 URL 重取
    robots.txt。``unknown``(无法确认)一律不放行;失败结果也按 host 缓存,本次任务
    内不再反复探测(下个任务会重试)。
    """
    parts = urlsplit(url)
    key = f"{parts.scheme.lower()}://{parts.netloc.lower()}"
    rules = cache.get(key)
    if rules is None:
        rules = fetch_rules(url, user_agent, timeout=timeout)
        cache[key] = rules
    return rules, rules.allowed(target_path(url))
