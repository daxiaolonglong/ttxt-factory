"""robots.txt 获取与解析。

安全原则:
- HTTP 200 → 按规则解析;
- 404/410 → 视为"无规则"(robots 缺席的标准语义是允许);
- 其他异常(5xx/网络错误/超时)→ `unknown=True`,调用方必须暂停该来源,不得放行。
解析范围:仅 `User-agent: *` 组;支持路径前缀、`*` 通配符与 `$` 行尾锚。
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass
class RobotsRules:
    allow_all: bool = True
    rules: list[tuple[bool, str]] = field(default_factory=list)  # (allow, pattern)
    crawl_delay_s: float | None = None
    unknown: bool = False

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


def fetch_robots(url: str, user_agent: str, timeout: float = 10) -> RobotsRules:
    parts = urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    request = urllib.request.Request(robots_url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status == 200:
                return parse_robots(response.read().decode("utf-8", errors="replace"))
            if response.status in (404, 410):
                return RobotsRules(allow_all=True)
            return RobotsRules(allow_all=False, unknown=True)
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410):
            return RobotsRules(allow_all=True)
        return RobotsRules(allow_all=False, unknown=True)
    except Exception:  # noqa: BLE001 - 无法确认规则 → 暂停来源,不放行
        return RobotsRules(allow_all=False, unknown=True)


def target_path(url: str) -> str:
    parts = urlsplit(url)
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
