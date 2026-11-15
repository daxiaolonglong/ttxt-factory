"""采集栈离线自检(P15)。

为什么要有这个模块:第三方抓取库(Scrapling/curl_cffi/patchright)的参数词表一旦
漂移,故障会在长时间采集跑到一半时才暴露,而且**伪装成"网络失败"**——任务记录里
只留下 fetch failed,看不出是 API 不对。所以每次体检都要离线把
"我们实际会传什么参数" 与 "库里声明能收什么参数" 对一遍,不需要联网。

三条硬口径:
- 只读:不联网、不启动浏览器、不写任何文件;
- 词表拿不到(版本结构变了)只能 WARN,不许假装 PASS;
- 浏览器可执行文件缺失是 FAIL:本机策略是用系统 Edge,绝不偷偷下载浏览器。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

from ttxt import config as cfg
from ttxt.crawler import fetcher, sources

#: 采集链路依赖的第三方包(缺一个就没法采集,体检要把版本写进日志)。
DIST_PACKAGES = ("scrapling", "curl-cffi", "patchright", "playwright", "browserforge", "lxml")
#: ``fetcher._scrapling_get`` 实际传给 ``Fetcher.get`` 的参数(它有 TypeError 降级链,故只 WARN)。
HTTP_KWARGS = ("timeout", "stealthy_headers")
#: 需要比对的 Scrapling 参数词表类型名。
PARAM_TYPE_NAMES = ("GetRequestParams", "StealthSession")


def dist_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in DIST_PACKAGES:
        try:
            versions[name] = metadata.version(name)
        except Exception:  # noqa: BLE001 - 包不存在/元数据损坏都要如实记录
            versions[name] = "MISSING"
    return versions


def allowed_params() -> dict[str, set[str]]:
    """Scrapling 声明的入参词表。

    只读 ``__annotations__``,不调 ``get_type_hints``:0.5.1 里这些 TypedDict 含
    未解析的前向引用(``Cookies``),``get_type_hints`` 会直接 NameError。
    """
    try:
        from scrapling.engines._browsers import _types as types_module  # noqa: PLC0415 - 可选依赖,缺失即词表不可用
    except Exception:  # noqa: BLE001 - 库缺失/结构变了都归为"词表拿不到"
        return {}
    table: dict[str, set[str]] = {}
    for name in PARAM_TYPE_NAMES:
        obj = getattr(types_module, name, None)
        if obj is None:
            continue
        table[name] = set((getattr(obj, "__annotations__", None) or {}).keys())
    return table


@dataclass
class StackReport:
    versions: dict[str, str] = field(default_factory=dict)
    allowed: dict[str, set[str]] = field(default_factory=dict)
    browser_kwargs: list[str] = field(default_factory=list)
    browser_kwargs_error: str = ""
    drift_browser: list[str] = field(default_factory=list)
    drift_http: list[str] = field(default_factory=list)
    executable: str = ""
    executable_ok: bool = False
    enabled_sources: int = 0
    browser_sources: int = 0
    profile_bytes: int = 0
    cap_bytes: int = 0
    notes: list[str] = field(default_factory=list)


def profile_bytes() -> int:
    from ttxt import housekeep  # noqa: PLC0415 - 只为体积统计,避免顶层循环依赖

    return int(housekeep.dir_size(fetcher.browser_profile_dir()))


def collect() -> StackReport:
    report = StackReport(versions=dist_versions(), allowed=allowed_params())
    config = cfg.load_config()
    browser_cfg = config.get("browser", {})
    report.executable = str(browser_cfg.get("executable_path") or "")
    report.executable_ok = bool(report.executable) and Path(report.executable).exists()

    try:
        all_sources = sources.load_sources()
    except Exception as exc:  # noqa: BLE001 - 来源文件坏了要让体检说话,而不是崩
        all_sources = []
        report.notes.append(f"来源配置读不出来:{type(exc).__name__}: {exc}")
    enabled = [item for item in all_sources if item.enabled and not item.demo]
    report.enabled_sources = len(enabled)
    report.browser_sources = sum(1 for item in enabled if getattr(item, "render", "http") == "browser")

    try:
        report.browser_kwargs = sorted(fetcher._browser_kwargs())
    except Exception as exc:  # noqa: BLE001 - 参数建不出来本身就是 FAIL 级问题
        report.browser_kwargs_error = f"{type(exc).__name__}: {exc}"

    session_keys = report.allowed.get("StealthSession")
    if session_keys is None:
        report.notes.append("拿不到 Scrapling 的 StealthSession 参数词表(版本结构变了),无法自动比对")
    else:
        report.drift_browser = sorted(set(report.browser_kwargs) - session_keys)
    http_keys = report.allowed.get("GetRequestParams")
    if http_keys is not None:
        report.drift_http = sorted(set(HTTP_KWARGS) - http_keys)

    report.cap_bytes = int(float(config.get("disk", {}).get("browser_max_gb", 3)) * 1024**3)
    try:
        report.profile_bytes = profile_bytes()
    except Exception as exc:  # noqa: BLE001 - 体积读不出来不影响其它结论
        report.notes.append(f"浏览器 profile 体积读不出来:{type(exc).__name__}: {exc}")
    return report


def checks(report: StackReport) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    versions_text = " ".join(f"{name}={value}" for name, value in report.versions.items())
    scrapling_ok = report.versions.get("scrapling", "MISSING") != "MISSING"
    rows.append(("PASS" if scrapling_ok else "FAIL", "crawler.stack.import", versions_text))

    if not report.allowed:
        rows.append(
            (
                "WARN",
                "crawler.stack.api_drift",
                "Scrapling 入参词表拿不到,无法自动核对升级后的参数;必须人工核验后再接采集:" + versions_text,
            )
        )
    elif report.drift_browser:
        rows.append(
            (
                "FAIL",
                "crawler.stack.api_drift",
                f"浏览器参数不在 Scrapling 词表内:{report.drift_browser} —— 会在采集中途伪装成网络失败",
            )
        )
    elif report.drift_http:
        rows.append(
            (
                "WARN",
                "crawler.stack.api_drift",
                f"HTTP 参数不在 Scrapling 词表内:{report.drift_http}(代码有降级重试,仍需核对)",
            )
        )
    else:
        rows.append(
            (
                "PASS",
                "crawler.stack.api_drift",
                "browser/http 实参全部命中词表(stealth_session_keys="
                f"{len(report.allowed.get('StealthSession', ()))}, "
                f"get_keys={len(report.allowed.get('GetRequestParams', ()))})",
            )
        )

    if report.browser_kwargs_error:
        rows.append(
            (
                "FAIL",
                "crawler.stack.browser",
                f"浏览器会话参数建不出来:{report.browser_kwargs_error}(executable={report.executable or '未配置'})",
            )
        )
    elif report.browser_sources == 0:
        rows.append(
            (
                "PASS",
                "crawler.stack.browser",
                f"当前没有 render=browser 的启用来源;executable={report.executable or '用库默认'}",
            )
        )
    elif not report.executable_ok:
        rows.append(
            (
                "FAIL",
                "crawler.stack.browser",
                f"{report.browser_sources} 个来源要浏览器渲染,但 browser.executable_path 不存在:"
                f"{report.executable or '未配置'}(本机口径是用系统 Edge,不下载额外浏览器)",
            )
        )
    else:
        rows.append(
            (
                "PASS",
                "crawler.stack.browser",
                f"executable={report.executable};浏览器来源={report.browser_sources};"
                f"磁盘缓存旗标={'有' if 'extra_flags' in report.browser_kwargs else '无(不设上限,靠 housekeep 兜底)'}",
            )
        )

    if report.enabled_sources == 0:
        rows.append(("WARN", "crawler.stack.sources", "没有任何启用的正式来源(demo 不算),采集无事可做"))
    else:
        rows.append(
            (
                "PASS",
                "crawler.stack.sources",
                f"启用正式来源={report.enabled_sources}(其中浏览器渲染={report.browser_sources})",
            )
        )

    if report.cap_bytes and report.profile_bytes > report.cap_bytes:
        rows.append(
            (
                "WARN",
                "crawler.stack.browser_disk",
                f"浏览器 profile {report.profile_bytes / 1048576:.0f}MB 已超限额 "
                f"{report.cap_bytes / 1073741824:.1f}GB,housekeep 会按最旧文件删;"
                "先核对 browser.disk_cache_mb 是否被关掉",
            )
        )
    else:
        rows.append(
            (
                "PASS",
                "crawler.stack.browser_disk",
                f"浏览器 profile {report.profile_bytes / 1048576:.0f}MB / 限额 "
                f"{report.cap_bytes / 1073741824:.1f}GB",
            )
        )

    if report.notes:
        rows.append(("WARN", "crawler.stack.notes", ";".join(report.notes)))
    return rows


def doctor_checks() -> list[tuple[str, str, str]]:
    """体检入口:返回 ``(level, name, detail)`` 列表。"""
    return checks(collect())
