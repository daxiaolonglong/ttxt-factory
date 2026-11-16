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

P32 安全边界(SSRF,不得为了采集成功率而放宽):
- 初始 URL 先过 ``url_safety.check_url``;本机/私网/链路本地/保留地址、坏协议一律终态拒绝。
- 静态抓取**显式** ``follow_redirects=False``,跳转链由本项目 ``url_safety.walk`` 逐跳校验后再取;
  若 Scrapling 版本不再接受该参数,直接终态报错,禁止回退到库默认跳转策略(那等于绕过逐跳校验)。
- 浏览器路径注册 ``page.route`` 拦截器(每个子请求都判定,含 iframe/弹窗/XHR),
  一旦出现 Service Worker 立刻 ``stop()``(SW 请求可能不走 ``route``);
  并给 Chromium 进程加 ``--host-resolver-rules`` 把本机/私网域名解析直接判死,作为拦截器
  漏网时的第二层。该层必须先恒等映射本机代理主机,否则本机代理(回环地址)会被打死,
  公网采集全断 —— 见 ``ssrf_host_resolver_rules`` 的注释与 07_tmp/p32 实测记录。
"""

from __future__ import annotations

import os
import shutil
import threading
import time
import weakref
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ttxt import config as cfg
from ttxt.crawler import url_safety
from ttxt.logging import get_logger

LOG = get_logger("crawler.fetcher")

#: 封禁/不存在类:重试无意义,直接终态,绝不继续 hammer 对方站点。
TERMINAL_STATUSES = frozenset({401, 403, 404, 410, 451})
#: 浏览器磁盘缓存默认上限(MB);配 ``browser.disk_cache_mb`` 覆盖,``<= 0`` 表示不加旗标(交给 housekeep 兜底)。
DEFAULT_DISK_CACHE_MB = 64
#: 临时故障:允许指数退避重试。
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Chromium 进程级兜底:把本机/私网主机名直接解析成 NOTFOUND。
#: 这是请求拦截器之外的第二层 —— Playwright 官方明确说明 Service Worker 发起的请求
#: 可能绕过 ``route`` 拦截,所以只靠拦截器不够(实测见 07_tmp/p32)。
#: 注意:本机代理(P32 实测 Windows 系统代理 = 127.0.0.1:57777)本身就是回环地址,
#: ``MAP 127.* ~NOTFOUND`` 会把代理主机一起打死,公网采集全断
#: (实测 ERR_PROXY_CONNECTION_FAILED)。因此规则前必须先恒等映射代理主机:
#: Chromium 的 host-resolver-rules **先命中先生效**。代价是第二层对代理主机这一条
#: 不设防,该主机上的内网防护完全依赖第一层拦截器(实测已拦)与 SW 停用工。
#: Chromium 的 host-resolver-rules **只接受不带方括号的 IPv6 写法**:
#: 实测(07_tmp/p32/probe_rules.py / probe_rules2.txt)一旦规则里出现 ``[``,
#: Chromium 判定语法非法并**丢弃整串规则**,导致 localhost、127.* 全部失效。
#: 因此这里禁止任何方括号,并在 ``ssrf_host_resolver_rules`` 里做启动期自检。
_PRIVATE_MAP_PATTERNS = (
    "127.*",
    "localhost",
    "*.localhost",
    "::1",
    "fd*",
    "fe8*",
    "0.*",
    "10.*",
    "192.168.*",
    "169.254.*",
    "172.16.*",
    "172.17.*",
    "172.18.*",
    "172.19.*",
    "172.20.*",
    "172.21.*",
    "172.22.*",
    "172.23.*",
    "172.24.*",
    "172.25.*",
    "172.26.*",
    "172.27.*",
    "172.28.*",
    "172.29.*",
    "172.30.*",
    "172.31.*",
)
_WIN_INET_SETTINGS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


def _split_proxy_host(value: str) -> str:
    """从 ``http=127.0.0.1:57777;https=...``/``socks5://h:1``/``127.0.0.1:57777`` 取主机名。"""
    entry = (value or "").strip()
    if not entry or entry.lower() == "<direct>":
        return ""
    head = entry.split("/", 1)[0]
    if "=" in head:
        entry = entry.split("=", 1)[1]
    entry = entry.split(";", 1)[0].strip()
    if not entry:
        return ""
    parsed = urlsplit(entry if "://" in entry else f"//{entry}")
    return (parsed.hostname or "").lower()


@lru_cache(maxsize=1)
def _browser_proxy_hosts() -> tuple[str, ...]:
    """浏览器实际会走的代理主机,取法与 Chromium 一致:先看环境变量,再看 Windows 系统代理。

    缓存按进程:常驻 worker 运行期间不改代理,重启后自然刷新。
    """
    hosts: list[str] = []
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        host = _split_proxy_host(os.environ.get(var, ""))
        if host:
            hosts.append(host)
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_INET_SETTINGS_KEY) as key:
            enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0] or 0)
            if enabled:
                server = str(winreg.QueryValueEx(key, "ProxyServer")[0] or "")
                for part in server.split(";"):
                    host = _split_proxy_host(part)
                    if host:
                        hosts.append(host)
    except OSError as exc:
        LOG.warn("crawl.browser.proxy_config_unreadable", error=str(exc)[:160])
    return tuple(dict.fromkeys(hosts))


def ssrf_host_resolver_rules() -> str:
    """构造 ``--host-resolver-rules``:先放行代理主机,再把本机/私网字面量判死。"""
    bad = [pattern for pattern in _PRIVATE_MAP_PATTERNS if "[" in pattern or "]" in pattern or "," in pattern]
    if bad:
        # 实测:串里出现方括号会让 Chromium 判定语法非法并丢掉**整串**规则,
        # 第二层直接失效。这种配置错误必须炸,不能静默带着废规则启动浏览器。
        raise FetchError(f"host-resolver-rules 模式非法(会导致整串规则被丢弃):{bad}", terminal=True)
    rules = [f"MAP {host} {host}" for host in _browser_proxy_hosts()]
    rules += [f"MAP {pattern} ~NOTFOUND" for pattern in _PRIVATE_MAP_PATTERNS]
    return ", ".join(rules)


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
    # 浏览器磁盘缓存必须在**源头**封顶:实测头条频道页一次访问就让 profile 涨 16 MB
    # (P15 smoke,07_tmp/p15_crawler_verify_20261115/smoke_net.json),长时间采集会把
    # housekeep 的 browser_max_gb 兜底逼到"按最旧文件删"——那是在删 Chromium profile
    # 内部文件,属于事后抢救,不能当常规手段(规则 30:磁盘不得暴涨)。
    cache_mb = int(browser_cfg.get("disk_cache_mb", DEFAULT_DISK_CACHE_MB))
    configured = [str(flag) for flag in (browser_cfg.get("extra_flags") or [])]
    foreign = [flag for flag in configured if flag.startswith("--host-resolver-rules=")]
    if foreign:
        # 安全边界不得被配置覆盖:自定义规则一律替换掉,只保留本项目这一份。
        LOG.warn("crawl.browser.host_resolver_rules_overridden", replaced="; ".join(foreign))
    flags = [flag for flag in configured if not flag.startswith("--host-resolver-rules=")]
    if cache_mb > 0:
        flags.append(f"--disk-cache-size={cache_mb * 1024 * 1024}")
    flags.append(f"--host-resolver-rules={ssrf_host_resolver_rules()}")
    kwargs["extra_flags"] = flags
    # page_setup 在 page.goto() 之前执行,是往页面挂请求拦截器的唯一入口。
    kwargs["page_setup"] = _install_request_guard
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


def _max_hops() -> int:
    return max(1, int(cfg.load_config().get("crawler", {}).get("max_redirects", url_safety.DEFAULT_MAX_HOPS)))


def _scrapling_get(url: str, timeout: float) -> Any:
    """单跳取静态 HTTP:显式关闭库的自动跳转。

    Scrapling 0.5.1 默认 ``follow_redirects="safe"`` 由 curl-impersonate 在库里判私网,
    但那是**库的行为**:换版本、换安装就变。P32 起跳转链由本项目自己走,每一跳都过
    ``url_safety``,所以这里必须拿到 3xx 而不是让库偷偷跟完。
    """
    from scrapling.fetchers import Fetcher

    try:
        return Fetcher.get(url, timeout=timeout, stealthy_headers=True, follow_redirects=False)
    except TypeError as exc:
        # 参数不再被接受 ⇒ 绝不能退回库默认的跳转策略,那等于绕过逐跳校验。
        raise FetchError(
            "Scrapling 不再接受显式 follow_redirects=False;禁止回退到库默认跳转策略"
            "(会绕过 SSRF 逐跳校验)。请先核查 Scrapling 版本再恢复采集。"
            f"原始错误:{exc}",
            terminal=True,
        ) from exc


def _http_hop(url: str, timeout: float) -> url_safety.HopResponse:
    page = _scrapling_get(url, timeout)
    headers: dict[str, str] = {}
    raw_headers = getattr(page, "headers", None) or {}
    try:
        items = list(raw_headers.items())
    except AttributeError:  # pragma: no cover - 结构异常时按无跳转处理
        items = []
    for key, value in items:
        headers[str(key).lower()] = str(value)
    return url_safety.HopResponse(status=int(getattr(page, "status", 200) or 200), headers=headers, payload=page)


def _fetch_static(url: str, timeout: float) -> Any:
    """静态抓取:逐跳校验 + 逐跳取,返回最终页面对象。"""
    result = url_safety.walk(url, fetch_hop=lambda hop_url: _http_hop(hop_url, timeout), max_hops=_max_hops())
    if len(result.hops) > 1:
        LOG.info(
            "crawl.fetch.redirect_chain",
            url=url,
            final=result.url,
            chain=" | ".join(f"{hop.status}->{hop.location or ''}" for hop in result.hops[:-1]),
            ips=",".join(result.hops[-1].ips),
        )
    return result.hop.payload


def _install_request_guard(page: Any) -> None:
    """给浏览器页面挂 SSRF 拦截器:主文档、XHR、iframe、弹窗、子资源每个请求都判定。

    放行时调用 ``route.fallback()``,把请求交回 Scrapling 自己注册的 ``**/*`` 拦截器
    (它负责 disable_resources / blocked_domains),不得截断它的行为。
    非网络协议(about:/blob:/data:)由浏览器本地处理,不经 IP 判定。
    """

    def handler(route: Any, request: Any) -> None:
        url = str(getattr(request, "url", "") or "")
        scheme = url.split(":", 1)[0].lower() if ":" in url else ""
        if scheme and scheme not in {*url_safety.ALLOWED_SCHEMES, *url_safety.WEBSOCKET_SCHEMES}:
            try:
                route.fallback()
            except Exception:  # noqa: BLE001 - 放行失败交给浏览器
                pass
            return
        verdict = url_safety.check_cached(url)
        if not verdict.allowed:
            LOG.warn(
                "crawl.browser.blocked_by_ssrf_guard",
                url=url[:200],
                reason=verdict.reason,
                ips=",".join(verdict.ips),
            )
            try:
                route.abort("blockedbyclient")
            except Exception as exc:  # noqa: BLE001 - 拦截器内部异常不得让整次渲染崩掉
                LOG.warn("crawl.browser.guard_abort_failed", url=url[:200], error=str(exc)[:160])
            return
        try:
            route.fallback()
        except Exception:  # noqa: BLE001 - 老版本没有 fallback 时退化为继续请求
            try:
                route.continue_()
            except Exception:
                pass

    page.route("**/*", handler)
    _hook_service_workers(page)
    LOG.info("crawl.browser.guard_installed")


#: 用弱引用记录挂过监听的 context:用 id() 记数会在对象回收后复用同一数值,导致漏挂。
_sw_hooked: "weakref.WeakSet[Any]" = weakref.WeakSet()
_sw_hook_lock = threading.Lock()


def _hook_service_workers(page: Any) -> None:
    """Service Worker 等效隔离:一旦出现就立刻停掉。

    Playwright/patchright 的 ``route`` 拦截不保证覆盖 SW 自己发起的请求,所以 SW 是拦截器的
    已知旁路。本项目不预期需要任何 SW(采集只读公开页面),因此出现即停:
    进程级 ``--host-resolver-rules`` 是同时兜住 SW 请求的第二层。
    """
    context = getattr(page, "context", None)
    if context is None:
        return
    try:
        with _sw_hook_lock:
            if context in _sw_hooked:
                return
            _sw_hooked.add(context)
        context.on("serviceworker", _stop_service_worker)
    except Exception as exc:  # noqa: BLE001 - 挂不上不影响主拦截器
        LOG.warn("crawl.browser.sw_hook_failed", error=str(exc)[:160])


def _stop_service_worker(worker: Any) -> None:
    url = str(getattr(worker, "script_url", "") or "")[:200]
    LOG.warn("crawl.browser.service_worker_stopped", url=url)
    try:
        worker.stop()
    except Exception as exc:  # noqa: BLE001
        LOG.warn("crawl.browser.service_worker_stop_failed", url=url, error=str(exc)[:160])


def _browser_get(url: str, session: Any | None) -> Any:
    if session is not None:
        return session.fetch(url)
    with browser_session() as owned:
        return owned.fetch(url)


def _fetch_once(url: str, timeout: float, render: bool, session: Any | None) -> Any:
    return _browser_get(url, session) if render else _fetch_static(url, timeout)


def _assert_start_allowed(url: str) -> None:
    """出网前的第一道判定:坏协议/本机/私网目标一律终态拒绝,不建立任何连接。"""
    verdict = url_safety.check_url(url)
    if verdict.allowed:
        return
    LOG.warn(
        "crawl.fetch.blocked_by_ssrf_guard",
        url=url[:200],
        reason=verdict.reason,
        ips=",".join(verdict.ips),
    )
    if verdict.dns_failed:
        # 解析失败按网络故障对待(允许重试),但同样不允许"解析不了就先连上再说"。
        raise FetchError(f"URL 解析失败,安全边界无法确认目标:{verdict.reason}")
    raise FetchError(f"目标被 SSRF 安全边界拒绝:{verdict.reason}", terminal=True)


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
    被 SSRF 安全边界拒绝的目标(初始 URL 或任一跳转跳)立即终态抛错,不重试、不连接。
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
            _assert_start_allowed(url)
            page = _fetch_once(url, timeout, render, session)
            status = int(getattr(page, "status", 200) or 200)
            if status in TERMINAL_STATUSES:
                raise FetchError(f"HTTP {status}(终态,不重试)", status=status, terminal=True)
            if status in RETRYABLE_STATUSES:
                raise FetchError(f"HTTP {status}", status=status)
            return FetchResult(url=url, status=status, page=page, elapsed_s=time.monotonic() - started)
        except url_safety.UnsafeURLError as exc:
            LOG.warn(
                "crawl.fetch.blocked_by_ssrf_guard",
                url=exc.url[:200],
                reason=exc.reason,
                hops=len(exc.hops),
            )
            if exc.dns_failed:
                last_error, last_status = exc, None
                if attempt < max_attempts:
                    wait = min(2**attempt, 8)
                    LOG.warn("crawl.fetch.retry", url=url, attempt=attempt, error=str(exc)[:200], wait_s=wait)
                    time.sleep(wait)
                continue
            raise FetchError(f"跳转链被 SSRF 安全边界拒绝:{exc.reason}", terminal=True) from exc
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
