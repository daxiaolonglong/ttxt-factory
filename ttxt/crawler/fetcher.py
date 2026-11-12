"""Scrapling 抓取封装与重试。"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

from ttxt import config as cfg


class FetchError(RuntimeError):
    pass


@dataclass
class FetchResult:
    url: str
    status: int
    page: Any  # Scrapling Response
    elapsed_s: float


def _scrapling_get(url: str, timeout: float) -> Any:
    from scrapling.fetchers import Fetcher

    try:
        return Fetcher.get(url, timeout=timeout, stealthy_headers=True)
    except TypeError:
        try:
            return Fetcher.get(url, timeout=timeout)
        except TypeError:
            return Fetcher.get(url)


def _browser_get(url: str) -> Any:
    from pathlib import Path

    from scrapling.fetchers import StealthyFetcher

    browser_cfg = cfg.load_config().get("browser", {})
    executable = browser_cfg.get("executable_path")
    if executable and not Path(executable).exists():
        raise FetchError(f"browser executable not found: {executable}")
    # 将浏览器临时目录重定向到项目内,避免写入系统 %TEMP%
    browser_tmp = cfg.TEMP_DIR / "browser-tmp"
    browser_tmp.mkdir(parents=True, exist_ok=True)
    previous_tmp = (os.environ.get("TMP"), os.environ.get("TEMP"))
    os.environ["TMP"] = str(browser_tmp)
    os.environ["TEMP"] = str(browser_tmp)
    try:
        return StealthyFetcher.fetch(
            url,
            headless=bool(browser_cfg.get("headless", True)),
            executable_path=executable or None,
            timeout=int(browser_cfg.get("timeout_ms", 60000)),
            network_idle=bool(browser_cfg.get("network_idle", True)),
        )
    finally:
        for key, value in zip(("TMP", "TEMP"), previous_tmp):
            if value is not None:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)


def _fetch_once(url: str, timeout: float, render: bool) -> Any:
    return _browser_get(url) if render else _scrapling_get(url, timeout)


def fetch_page(url: str, timeout: float | None = None, render: bool = False) -> FetchResult:
    crawler_cfg = cfg.load_config().get("crawler", {})
    if timeout is None:
        timeout = float(crawler_cfg.get("http_timeout_s", 30))
    max_attempts = int(crawler_cfg.get("max_attempts", 3))
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        started = time.monotonic()
        try:
            page = _fetch_once(url, timeout, render)
            status = int(getattr(page, "status", 200) or 200)
            if status >= 500:
                raise FetchError(f"HTTP {status}")
            return FetchResult(url=url, status=status, page=page, elapsed_s=time.monotonic() - started)
        except Exception as exc:  # noqa: BLE001 - 统一重试
            last_error = exc
            if attempt < max_attempts:
                time.sleep(min(2 ** attempt, 8))
    raise FetchError(f"fetch failed after {max_attempts} attempts: {last_error}")


def page_bytes(page: Any) -> bytes:
    body = getattr(page, "body", None)
    if isinstance(body, (bytes, bytearray)):
        return bytes(body)
    text = getattr(page, "text", "") or ""
    return str(text).encode("utf-8", errors="replace")


def page_text(page: Any) -> str:
    body = page_bytes(page)
    return body.decode("utf-8", errors="replace")
