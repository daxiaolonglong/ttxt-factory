"""P15:采集栈离线自检(``ttxt.crawler.stackcheck``)与浏览器磁盘缓存护栏。

这里守的是两类以前"看不见"的故障:
1. Scrapling/curl_cffi 升级后参数词表漂移 —— 会在采集中途伪装成网络失败;
2. 浏览器 profile 无限膨胀 —— 一次头条频道页实测涨 16MB,兜底靠删 profile 内部文件。
"""

from __future__ import annotations

import pytest

from ttxt import config as cfg, doctor
from ttxt.crawler import fetcher, sources, stackcheck


def _config(
    monkeypatch,
    *,
    executable: str | None = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    extra_flags: list[str] | None = None,
    disk_cache_mb: int | None = 64,
    browser_max_gb: float = 3,
) -> None:
    browser: dict[str, object] = {"enabled": True, "headless": True, "page_pool": 2}
    if executable is not None:
        browser["executable_path"] = executable
    if extra_flags is not None:
        browser["extra_flags"] = extra_flags
    if disk_cache_mb is not None:
        browser["disk_cache_mb"] = disk_cache_mb
    monkeypatch.setattr(
        cfg,
        "load_config",
        lambda: {"browser": browser, "disk": {"browser_max_gb": browser_max_gb}},
    )


def _fake_sources(monkeypatch, *items: sources.Source) -> None:
    monkeypatch.setattr(stackcheck.sources, "load_sources", lambda path=None: list(items))


def _source(source_id: str, render: str = "http") -> sources.Source:
    return sources.Source(
        id=source_id,
        name=source_id,
        category="technology",
        kind="html",
        url="https://example.com/",
        render=render,
    )


def _quiet_report(monkeypatch) -> None:
    """把体积统计这种昂贵/依赖真实目录的动作钉掉,只留下被测逻辑。"""
    monkeypatch.setattr(stackcheck, "profile_bytes", lambda: 10 * 1024 * 1024)


# ---------------------------------------------------------------- 磁盘缓存护栏
def test_browser_kwargs_cap_disk_cache_by_default(monkeypatch):
    _config(monkeypatch)
    kwargs = fetcher._browser_kwargs()
    assert "--disk-cache-size=67108864" in kwargs["extra_flags"]


def test_disk_cache_flag_can_be_turned_off(monkeypatch):
    _config(monkeypatch, disk_cache_mb=0)
    kwargs = fetcher._browser_kwargs()
    flags = kwargs.get("extra_flags") or []
    assert not any(str(flag).startswith("--disk-cache-size=") for flag in flags), "关掉磁盘缓存时必须撤掉对应旗标"
    assert any(str(flag).startswith("--host-resolver-rules=") for flag in flags), "撤磁盘缓存旗标不得顺手撤掉 SSRF 兜底规则(P32)"


def test_config_extra_flags_are_kept_and_cache_flag_appended(monkeypatch):
    _config(monkeypatch, extra_flags=["--lang=zh-CN"])
    flags = fetcher._browser_kwargs()["extra_flags"]
    assert flags[0] == "--lang=zh-CN"
    assert "--disk-cache-size=67108864" in flags


# ---------------------------------------------------------------- 词表读取口径
def test_allowed_params_reads_installed_annotations_without_get_type_hints():
    pytest.importorskip("scrapling")
    table = stackcheck.allowed_params()
    assert "StealthSession" in table and "GetRequestParams" in table
    # 0.5.1 实测:user_data_dir/max_pages/network_idle 都在会话词表里;
    # 用 get_type_hints 读会 NameError(Cookies 前向引用),这里必须是纯 __annotations__。
    assert {"user_data_dir", "max_pages", "network_idle"} <= table["StealthSession"]


def test_installed_stack_has_no_browser_kwarg_drift():
    pytest.importorskip("scrapling")
    rows = {name: (level, detail) for level, name, detail in stackcheck.doctor_checks()}
    level, detail = rows["crawler.stack.api_drift"]
    assert level == "PASS", detail


# ---------------------------------------------------------------- 漂移必须说话
def test_browser_kwarg_drift_is_fail(monkeypatch):
    _config(monkeypatch)
    _quiet_report(monkeypatch)
    _fake_sources(monkeypatch, _source("s1"))
    monkeypatch.setattr(
        fetcher,
        "_browser_kwargs",
        lambda: {"headless": True, "totally_bogus": 1},
    )
    monkeypatch.setattr(
        stackcheck,
        "allowed_params",
        lambda: {"StealthSession": {"headless"}, "GetRequestParams": {"timeout", "stealthy_headers"}},
    )
    rows = {name: (level, detail) for level, name, detail in stackcheck.doctor_checks()}
    level, detail = rows["crawler.stack.api_drift"]
    assert level == "FAIL"
    assert "totally_bogus" in detail


def test_http_kwarg_drift_only_warns_because_code_has_fallback(monkeypatch):
    _config(monkeypatch)
    _quiet_report(monkeypatch)
    _fake_sources(monkeypatch, _source("s1"))
    monkeypatch.setattr(stackcheck, "allowed_params", lambda: {"GetRequestParams": {"timeout"}})
    monkeypatch.setattr(fetcher, "_browser_kwargs", lambda: {"headless": True})
    rows = {name: (level, detail) for level, name, detail in stackcheck.doctor_checks()}
    assert rows["crawler.stack.api_drift"][0] == "WARN"
    assert "stealthy_headers" in rows["crawler.stack.api_drift"][1]


def test_missing_word_table_must_not_claim_pass(monkeypatch):
    _config(monkeypatch)
    _quiet_report(monkeypatch)
    _fake_sources(monkeypatch, _source("s1"))
    monkeypatch.setattr(stackcheck, "allowed_params", lambda: {})
    rows = {name: (level, detail) for level, name, detail in stackcheck.doctor_checks()}
    assert rows["crawler.stack.api_drift"][0] == "WARN"
    assert rows["crawler.stack.notes"][0] == "WARN"
    assert "词表" in rows["crawler.stack.notes"][1]


def test_missing_scrapling_package_is_fail(monkeypatch):
    _config(monkeypatch)
    _quiet_report(monkeypatch)
    _fake_sources(monkeypatch, _source("s1"))
    monkeypatch.setattr(stackcheck, "allowed_params", lambda: {})
    monkeypatch.setattr(
        stackcheck,
        "dist_versions",
        lambda: {name: "MISSING" for name in stackcheck.DIST_PACKAGES},
    )
    rows = {name: (level, detail) for level, name, detail in stackcheck.doctor_checks()}
    assert rows["crawler.stack.import"][0] == "FAIL"


# ---------------------------------------------------------------- 浏览器/来源/磁盘
def test_missing_executable_with_browser_sources_is_fail(monkeypatch):
    missing = str(r"D:\ttxt\07_tmp\不存在的浏览器.exe")
    _config(monkeypatch, executable=missing)
    _quiet_report(monkeypatch)
    _fake_sources(monkeypatch, _source("s_browser", render="browser"))
    rows = {name: (level, detail) for level, name, detail in stackcheck.doctor_checks()}
    level, detail = rows["crawler.stack.browser"]
    assert level == "FAIL"
    assert missing in detail


def test_no_browser_sources_is_pass_without_executable(monkeypatch):
    _config(monkeypatch, executable=None)
    _quiet_report(monkeypatch)
    _fake_sources(monkeypatch, _source("s_rss"))
    rows = {name: (level, detail) for level, name, detail in stackcheck.doctor_checks()}
    assert rows["crawler.stack.browser"][0] == "PASS"


def test_no_enabled_sources_warns(monkeypatch):
    _config(monkeypatch)
    _quiet_report(monkeypatch)
    _fake_sources(monkeypatch)
    rows = {name: (level, detail) for level, name, detail in stackcheck.doctor_checks()}
    assert rows["crawler.stack.sources"][0] == "WARN"


def test_browser_profile_over_cap_warns_and_points_at_cache_flag(monkeypatch):
    _config(monkeypatch, browser_max_gb=1)
    monkeypatch.setattr(stackcheck, "profile_bytes", lambda: 3 * 1024**3)
    _fake_sources(monkeypatch, _source("s1"))
    rows = {name: (level, detail) for level, name, detail in stackcheck.doctor_checks()}
    level, detail = rows["crawler.stack.browser_disk"]
    assert level == "WARN"
    assert "disk_cache_mb" in detail


def test_source_file_unreadable_is_reported_not_crash(monkeypatch):
    _config(monkeypatch)
    _quiet_report(monkeypatch)

    def boom(path=None):
        raise ValueError("sources.json 坏了")

    monkeypatch.setattr(stackcheck.sources, "load_sources", boom)
    rows = {name: (level, detail) for level, name, detail in stackcheck.doctor_checks()}
    assert rows["crawler.stack.notes"][0] == "WARN"
    assert "sources.json 坏了" in rows["crawler.stack.notes"][1]


# ---------------------------------------------------------------- 体检接线
def test_doctor_reports_crawler_stack_section(isolated_data_root, capsys, monkeypatch):
    """真实体检里必须出现采集栈段落(用生产配置的 tmp 副本,不碰生产库)。"""
    monkeypatch.setattr(stackcheck, "profile_bytes", lambda: 5 * 1024 * 1024)
    assert doctor.run_doctor() in (0, 1)
    printed = capsys.readouterr().out
    for name in ("crawler.stack.import", "crawler.stack.api_drift", "crawler.stack.browser"):
        assert name in printed
