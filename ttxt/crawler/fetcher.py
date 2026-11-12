"""Scrapling 抓取封装:P12 起浏览器会话复用 + 状态码分级重试/终态策略。

设计要点(对应 P12 第一批缺陷 ①③④⑧⑨):
- 浏览器路径使用 Scrapling ``StealthySession``:一次采集只启动一个浏览器,
  页面池大小 = config ``browser.page_pool``(默认 2,允许 1-2);嵌套调用共享同一会话。
- 浏览器 profile 固定 ``02_crawler/.browser/profile``(项目内),housekeep 的
  browser_max_gb 限额由此真正生效。Playwright 的临时产物目录(os.tmpdir())也强制
  指向 ``02_crawler/.browser/pw-artifacts``:实测系统 %TEMP% 下 ``mkdtemp`` 会
  EPERM,且项目硬约束要求一切临时文件只落 ``D:\ttxt``。开新会话时顺带清理
  该目录下超过 24h 的陈旧子目录(临时文件 TTL)。
- 状态码策略:401/403/404/410/451 终态(不重试,直接失败入任务记录);
  429 与 5xx/网络/超时按 ``crawler.max_attempts`` 退避重试;429 退避更长。
- HTTP 路径仍用 ``Fetcher``(curl_cffi,会话开销可忽略)。
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ttxt import config as cfg
from ttxt.logging import get_logger

LOG = get_logger("crawler.fetcher")

#: 封禁/不存在类:重试无意义,直接终态,绝不继续 hammer 对方站点。
TERMINAL_STATUSES = frozenset({401, 403, 404, 410, 451})
#: 临时故障:允许指数退避重试。
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class FetchError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, terminal: bool = False):
        super().__init__(message)
        self.status = status
        self.terminal = terminal


@dataclass
class FetchResult:
    url: str
    status: int
    page: Any  # Scrapling Response
    elapsed_s: float


def browser_profile_dir() -> Path:
    """浏览器 profile 固定项目内(02_crawler/.browser/profile),受 browser_max_gb 限额管辖。"""
    target = cfg.CRAWLER_DIR / ".browser" / "profile"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _clear_profile_locks(profile: Path) -> None:
    """上次进程被强杀后 Chromium 系会残留 Singleton* 锁文件,清理后允许重新开会话。"""
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock = profile / name
        try:
            if lock.is_symlink() or lock.exists():
                lock.unlink()
                LOG.warn("crawl.browser.lock_cleared", path=str(lock))
        except OSError:
            pass


def _browser_kwargs() -> dict[str, Any]:
    browser_cfg = cfg.load_config().get("browser", {})
    executable = browser_cfg.get("executable_path")
    if executable and not Path(executable).exists():
        raise FetchError(f"browser executable not found: {executable}", terminal=True)
    pool = max(1, int(browser_cfg.get("page_pool", 2)))
    profile = browser_profile_dir()
    kwargs: dict[str, Any] = {
        "headless": bool(browser_cfg.get("headless", True)),
        "max_pages": pool,
        "timeout": int(browser_cfg.get("timeout_ms", 60000)),
        "network_idle": bool(browser_cfg.get("network_idle", True)),
        "user_data_dir": str(profile),
    }
    if executable:
        kwargs["executable_path"] = executable
    return kwargs


def _pw_artifacts_dir() -> Path:
    """Playwright 临时产物目录:强制项目内(系统 %TEMP% 实测 EPERM,且违反项目约束)。

    打开新会话时清理超过 24 小时的陈旧子目录(尽力而为,失败不影响采集)。
    """
    target = cfg.CRAWLER_DIR / ".browser" / "pw-artifacts"
    target.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 24 * 3600
    try:
        for child in target.iterdir():
            try:
                if child.is_dir() and child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:
                continue
    except OSError:
        pass
    return target


def _open_session() -> Any:
    from scrapling.fetchers import StealthySession

    # Playwright driver 用 os.tmpdir() 建 artifacts;Windows 上 TMP/TEMP/TMPDIR
    # 全部钉到项目内目录,既避开系统 TEMP 的 EPERM,也满足"临时文件只在 D:\ttxt"。
    pw_tmp = str(_pw_artifacts_dir())
    for var in ("TMP", "TEMP", "TMPDIR"):
        os.environ[var] = pw_tmp

    kwargs = _browser_kwargs()
    profile = browser_profile_dir()
    try:
        session = StealthySession(**kwargs)
        session.start()
    except Exception as first_exc:  # noqa: BLE001 - 可能是上次强杀残留的 profile 锁
        _clear_profile_locks(profile)
        try:
            session = StealthySession(**kwargs)
            session.start()
        except Exception:
            raise FetchError(f"browser session start failed: {first_exc}") from first_exc
    LOG.info(
        "crawl.browser_session.open",
        page_pool=kwargs["max_pages"],
        profile=kwargs["user_data_dir"],
    )
    return session


def _close_session(session: Any) -> None:
    try:
        session.close()
    except Exception as exc:  # noqa: BLE001 - 关闭失败只记录,不影响任务结果
        LOG.warn("crawl.browser_session.close_error", error=str(exc))
    LOG.info("crawl.browser_session.close")


_session_lock = threading.Lock()
_active: dict[str, Any] = {}


@contextmanager
def browser_session():
    """共享浏览器会话:同线程嵌套(如 runner→toutiao.collect)只计一次引用。

    最外层进入时启动唯一会话,最外层退出时关闭;期间所有 render=browser 的
    fetch_page 复用该会话的页面池。采集 Worker 并发固定为 1,这里加锁只是
    防御性护栏。
    """
    with _session_lock:
        if not _active:
            _active["session"] = _open_session()
        _active["users"] = int(_active.get("users", 0)) + 1
    # 锁必须在 yield 之外:runner→toutiao 同线程嵌套若持锁会自锁死。
    try:
        yield _active["session"]
    finally:
        with _session_lock:
            users = int(_active.get("users", 0)) - 1
            if users <= 0:
                session = _active.pop("session", None)
                # 必须整体清空:残留 {"users":0} 会让下次进入误判已有会话 → KeyError
                _active.clear()
                if session is not None:
                    _close_session(session)
            else:
                _active["users"] = users


def session_context(session: Any):
    """已有会话直接复用;没有会话时开一个临时会话(供单页抓取/测试探针)。"""
    return nullcontext(session) if session is not None else browser_session()


def _scrapling_get(url: str, timeout: float) -> Any:
    from scrapling.fetchers import Fetcher

    try:
        return Fetcher.get(url, timeout=timeout, stealthy_headers=True)
    except TypeError:
        try:
            return Fetcher.get(url, timeout=timeout)
        except TypeError:
            return Fetcher.get(url)


def _browser_get(url: str, session: Any | None) -> Any:
    if session is not None:
        return session.fetch(url)
    with browser_session() as owned:
        return owned.fetch(url)


def _fetch_once(url: str, timeout: float, render: bool, session: Any | None) -> Any:
    return _browser_get(url, session) if render else _scrapling_get(url, timeout)


def fetch_page(
    url: str,
    timeout: float | None = None,
    render: bool = False,
    session: Any | None = None,
) -> FetchResult:
    """抓取一个 URL。

    返回 2xx/3xx/一般 4xx(非终态集合外)视为成功;
    终态状态码(401/403/404/410/451)立即抛 ``FetchError(terminal=True)``;
    429/5xx/网络错误按 max_attempts 退避重试(429 退避加倍),耗尽后抛错。
    """
    crawler_cfg = cfg.load_config().get("crawler", {})
    if timeout is None:
        timeout = float(crawler_cfg.get("http_timeout_s", 30))
    max_attempts = max(1, int(crawler_cfg.get("max_attempts", 3)))
    last_error: BaseException | None = None
    last_status: int | None = None
    for attempt in range(1, max_attempts + 1):
        started = time.monotonic()
        try:
            page = _fetch_once(url, timeout, render, session)
            status = int(getattr(page, "status", 200) or 200)
            if status in TERMINAL_STATUSES:
                raise FetchError(f"HTTP {status}(终态,不重试)", status=status, terminal=True)
            if status in RETRYABLE_STATUSES:
                raise FetchError(f"HTTP {status}", status=status)
            return FetchResult(url=url, status=status, page=page, elapsed_s=time.monotonic() - started)
        except FetchError as exc:
            last_error, last_status = exc, exc.status
            if exc.terminal:
                raise
            if attempt < max_attempts:
                wait = 10 * attempt if exc.status == 429 else min(2**attempt, 8)
                LOG.warn(
                    "crawl.fetch.retry",
                    url=url,
                    attempt=attempt,
                    status=exc.status,
                    wait_s=wait,
                )
                time.sleep(wait)
        except Exception as exc:  # noqa: BLE001 - 网络/超时/渲染失败可重试
            last_error, last_status = exc, None
            if attempt < max_attempts:
                wait = min(2**attempt, 8)
                LOG.warn("crawl.fetch.retry", url=url, attempt=attempt, error=str(exc)[:200], wait_s=wait)
                time.sleep(wait)
    detail = f"HTTP {last_status}" if last_status else f"{type(last_error).__name__}: {last_error}"
    raise FetchError(f"fetch failed after {max_attempts} attempts: {detail}", status=last_status)


def page_bytes(page: Any) -> bytes:
    body = getattr(page, "body", None)
    if isinstance(body, (bytes, bytearray)):
        return bytes(body)
    text = getattr(page, "text", "") or ""
    return str(text).encode("utf-8", errors="replace")


def page_text(page: Any) -> str:
    body = page_bytes(page)
    return body.decode("utf-8", errors="replace")
