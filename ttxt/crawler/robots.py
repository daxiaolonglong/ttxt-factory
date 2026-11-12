"""robots.txt 获取与解析(简化长匹配规则)。"""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from ttxt import config as cfg


@dataclass
class RobotsRules:
    allow_all: bool = True
    rules: list[tuple[bool, str]] = field(default_factory=list)  # (allow, path)
    crawl_delay_s: float | None = None

    def allowed(self, path: str) -> bool:
        if self.allow_all:
            return True
        best_len = -1
        verdict = True
        for allow, rule_path in self.rules:
            if not rule_path:
                continue
            if path.startswith(rule_path) and len(rule_path) > best_len:
                best_len = len(rule_path)
                verdict = allow
        return verdict


def parse_robots(text: str) -> RobotsRules:
    rules = RobotsRules(allow_all=False)
    active_agent = False
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            active_agent = value == "*"
        elif active_agent and key == "disallow":
            rules.rules.append((False, value))
        elif active_agent and key == "allow":
            rules.rules.append((True, value))
        elif active_agent and key == "crawl-delay":
            try:
                rules.crawl_delay_s = float(value)
            except ValueError:
                pass
    if not rules.rules:
        rules.allow_all = True
    return rules


def fetch_robots(url: str, user_agent: str, timeout: float = 10) -> RobotsRules:
    parts = urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    request = urllib.request.Request(robots_url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return RobotsRules(allow_all=True)
            return parse_robots(response.read().decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 - robots 不可用时按允许处理(常见惯例)
        return RobotsRules(allow_all=True)


def target_path(url: str) -> str:
    parts = urlsplit(url)
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
