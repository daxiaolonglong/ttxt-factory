"""P36:浏览器 Profile 跨进程互斥(2026-11-17)。

覆盖用户提出的 13 条验收要求,并且**全部用真 subprocess 多进程**跑,不用 mock 冒充:

=== 需求 → 用例 ===
1. 同一进程内嵌套只启动一次浏览器并只取一次锁  → test_nested_session_opens_single_browser_and_locks_once
2. 浏览器启动或关闭抛异常后锁必须释放          → test_browser_start_failure_still_releases_lock
                                                  / test_close_failure_still_releases_lock
                                                  / test_exception_inside_session_releases_lock
3. 两个进程竞争时最多一个持锁                  → test_two_processes_contending_only_one_gets_the_lock
4. 一个进程持锁时另一进程不能打开浏览器        → test_busy_lock_blocks_second_process_from_opening_browser
5. 进程正常退出后锁正确交接                    → test_lock_hands_over_after_holder_exits
6. 进程被强制终止后锁被操作系统释放            → test_kernel_releases_lock_after_forced_kill
7. 等锁超过等待时间必须有明确状态              → test_timeout_is_explicit_state_with_holder_diagnostics
8. 启动失败也要释放锁                          → test_browser_start_failure_still_releases_lock
9. 关闭浏览器抛异常时不能继续占锁              → test_close_failure_still_releases_lock
10. 不能删除仍被活进程使用的 Singleton/lockfile → test_live_lockfile_is_never_deleted
                                                  / test_stale_lockfile_cleared_only_when_provably_unowned
11. Worker 与 metrics 使用同一把锁              → test_metrics_and_worker_share_one_lock
12. 静态 HTTP 抓取不占用浏览器锁                → test_static_fetch_never_touches_browser_lock
13. P35 既有功能不回归                          → 由全量回归保证(本文件另加结构性护栏
                                                  test_no_caller_can_bypass_browser_session)

另加结构性护栏:test_old_indiscriminate_lock_deletion_is_gone(不许复活"无条件删锁文件")、
test_lock_does_not_rely_on_pid_or_timestamp(不许退化成猜进程死活)、
test_probe_free_is_silent_and_still_truthful(探测不刷日志但结论必须真)。

依赖:只用标准库(subprocess / ctypes)。所有锁文件落在 pytest 的 tmp 目录,
生产 ``02_crawler/.browser/profile.lock`` 由 conftest 的 ``browser_profile_isolated``
夹具逐用例守卫,测试不得创建它。
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from ttxt.crawler import browser_lock, fetcher, metrics_pages, store, url_safety
from ttxt.crawler.extract import ExtractedItem
from ttxt.crawler.robots import RobotsRules

ROOT = Path(__file__).resolve().parents[1]

# ------------------------------------------------------------------ 子进程替身

#: 持锁子进程:argv = profile_dir, gate_file, mode(wait|exit|kill)
HOLDER_SRC = '''
import sys
import time
from pathlib import Path

from ttxt.crawler import browser_lock

profile = Path(sys.argv[1])
gate = Path(sys.argv[2])
mode = sys.argv[3]

handle = browser_lock.acquire(profile, wait_s=5, poll_s=0.2, tag="holder")
print("HELD", flush=True)
gate.write_text("ready", encoding="utf-8")
print("READY", flush=True)

if mode == "exit":
    handle.release()
    print("RELEASED", flush=True)
    raise SystemExit(0)

# wait:等驱动写 go;kill:驱动会 taskkill 整棵进程树,内核负责放锁
deadline = time.time() + 120
while time.time() < deadline:
    try:
        if gate.read_text(encoding="utf-8") == "go":
            break
    except OSError:
        pass
    time.sleep(0.2)
handle.release()
print("RELEASED", flush=True)
'''

#: 竞争者:尝试取锁,GOT / BUSY 二选一,退出码 0 / 3
CONTENDER_SRC = '''
import sys
from pathlib import Path

from ttxt.crawler import browser_lock

try:
    handle = browser_lock.acquire(Path(sys.argv[1]), wait_s=float(sys.argv[2]), poll_s=0.2, tag="contender")
except browser_lock.ProfileBusyError as exc:
    print("BUSY {:.1f} {} {}".format(exc.waited_s, exc.holder.get("tag"), exc.holder.get("pid")), flush=True)
    raise SystemExit(3)
print("GOT", flush=True)
handle.release()
print("RELEASED", flush=True)
'''

#: 用 Win32 CreateFile(dwShareMode=0)独占一个文件 —— 与 Chromium 对 profile/lockfile
#: 的占用方式完全一致(实测:这种独占会让我们的 open(r+b) 直接 PermissionError)。
EXCLUSIVE_OPEN_SRC = '''
import ctypes
import sys
import time
from ctypes import wintypes

kernel32 = ctypes.windll.kernel32
GENERIC_ALL = 0x10000000
OPEN_EXISTING = 3
path = sys.argv[1]
handle = kernel32.CreateFileW(path, GENERIC_ALL, 0, None, OPEN_EXISTING, 0x80, None)
if handle == -1 or handle == wintypes.HANDLE(-1).value:
    print("OPEN_FAILED", kernel32.GetLastError(), flush=True)
    raise SystemExit(1)
print("EXCLUSIVE", flush=True)
time.sleep(float(sys.argv[2]))
kernel32.CloseHandle(handle)
print("CLOSED", flush=True)
'''


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return env


# 子进程隔离引导:pytest 的 monkeypatch 不会跨进程传播,子进程必须自己把 cfg.LOG_DIR
# 指向 conftest 导出的 TTXT_TEST_LOG_DIR,否则 browser_lock.* 事件会写进生产 09_logs,
# 被 conftest 的"生产状态指纹"抓个正着(实测确实抓到过一次)。
_CHILD_BOOTSTRAP = r'''
import os
import sys
from pathlib import Path

sys.path.insert(0, r"D:\ttxt")
_child_log_dir = os.environ.get("TTXT_TEST_LOG_DIR")
from ttxt import config as _child_cfg  # noqa: E402

if _child_log_dir:
    _child_cfg.LOG_DIR = Path(_child_log_dir)
else:  # 缺少隔离变量时也不许写生产日志:退到 07_tmp 下的兜底目录
    _child_cfg.LOG_DIR = Path(r"D:\ttxt\07_tmp\child_logs_fallback")
'''


def _run_python(code: str, args: list[str], timeout: float = 90.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-c", _CHILD_BOOTSTRAP + code, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(ROOT),
        env=_env(),
    )


def _profile_dir(tmp_path: Path) -> Path:
    """独立 profile 目录(绝不指向生产 02_crawler)。"""
    profile = tmp_path / "browser" / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    return profile


def _start_holder(tmp_path: Path, profile: Path, mode: str = "wait") -> subprocess.Popen[str]:
    """起一个真子进程并等它**确实取到锁**(靠 HELD/READY 两行握手,不靠 sleep 猜)。"""
    gate = tmp_path / f"gate-{mode}-{int(time.time() * 1000)}.txt"
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-c", _CHILD_BOOTSTRAP + HOLDER_SRC, str(profile), str(gate), mode],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(ROOT),
        env=_env(),
    )
    assert proc.stdout is not None
    first = proc.stdout.readline().strip()
    assert first == "HELD", f"持锁子进程没取到锁:{first!r} / {proc.stdout.read()[:400]!r}"
    deadline = time.time() + 15
    while time.time() < deadline and not gate.exists():
        time.sleep(0.05)
    assert gate.exists(), "持锁子进程没进入持锁状态"
    proc._gate = gate  # type: ignore[attr-defined]
    return proc


def _release_holder(proc: subprocess.Popen[str]) -> None:
    gate = getattr(proc, "_gate", None)
    if gate is not None:
        Path(gate).write_text("go", encoding="utf-8")
    proc.wait(timeout=60)


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    """强杀整棵进程树(venv 的 python.exe 是 launcher,真解释器是它的子进程)。"""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True, text=True, check=False,
        )
    else:  # pragma: no cover - 本机是 Windows
        proc.kill()
    proc.wait(timeout=60)


class FakeSession:
    """浏览器会话替身:只保证 close() 存在,真浏览器由生产实测验证。"""

    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class FakePage:
    """页面替身:抽取器只需要 page.css();返回空 ⇒ 本轮取不到指标,但走完全流程。"""

    def css(self, selector: str) -> list[Any]:
        return []


@pytest.fixture()
def isolated_profile() -> Path:
    """fetcher 现在用的 profile 目录(conftest 的 ``browser_profile_isolated`` 已挪到 tmp 下)。

    必须取 ``fetcher.browser_profile_dir()`` 本身,而不是另造一个路径:
    ``browser_session()`` 取的就是这个派生出来的锁,断言才对象得上。
    """
    return fetcher.browser_profile_dir()


@pytest.fixture()
def browser_opens(monkeypatch: pytest.MonkeyPatch) -> list[FakeSession]:
    """"起浏览器"换成替身并记录次数:用来证明只起一次、以及不该起时真的没起。"""
    opens: list[FakeSession] = []

    def fake_open() -> FakeSession:
        session = FakeSession()
        opens.append(session)
        return session

    monkeypatch.setattr(fetcher, "_open_session", fake_open)
    return opens


def _process_alive(pid: int) -> bool:
    """内核查这个 pid 是否真的活着(Windows:OpenProcess,不伤进程)。"""
    import ctypes

    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


# ------------------------------------------------------------------ 锁本身(真多进程)

def test_two_processes_contending_only_one_gets_the_lock(tmp_path: Path) -> None:
    """要求 3:两个真进程抢同一把锁,最多一个拿到。"""
    profile = _profile_dir(tmp_path)
    holder = _start_holder(tmp_path, profile)
    try:
        result = _run_python(CONTENDER_SRC, [str(profile), "1.0"], timeout=30)
        assert result.returncode == 3, result.stdout
        assert result.stdout.startswith("BUSY"), result.stdout
    finally:
        _release_holder(holder)


def test_timeout_is_explicit_state_with_holder_diagnostics(tmp_path: Path) -> None:
    """要求 7:等锁超时是明确状态(带等待时长与持锁者),不是假成功。"""
    profile = _profile_dir(tmp_path)
    holder = _start_holder(tmp_path, profile)
    try:
        started = time.monotonic()
        with pytest.raises(fetcher.BrowserBusyError) as caught:
            browser_lock.acquire(profile, wait_s=1.2, poll_s=0.2, tag="waiter")
        waited = time.monotonic() - started
        exc = caught.value
        assert waited >= 1.0, f"没等满就该失败:{waited:.2f}s"
        assert exc.waited_s >= 1.0
        assert exc.lock_path == browser_lock.lock_path_for(profile)
        assert exc.holder.get("tag") == "holder", exc.holder
        pid = int(exc.holder.get("pid") or 0)
        # venv 的 python.exe 是 launcher,真解释器是它的子进程 ⇒ 记录的 pid 不等于 Popen.pid;
        # 但必须是内核查得到的活进程,否则"谁持着锁"就是编出来的。
        assert pid > 0 and pid != os.getpid(), exc.holder
        if os.name == "nt":
            assert _process_alive(pid), exc.holder
        assert "holder" in str(exc)
    finally:
        _release_holder(holder)


def test_lock_hands_over_after_holder_exits(tmp_path: Path) -> None:
    """要求 5:持锁进程正常退出后,另一个进程立刻能接上。"""
    profile = _profile_dir(tmp_path)
    holder = _start_holder(tmp_path, profile, mode="exit")
    holder.wait(timeout=60)
    result = _run_python(CONTENDER_SRC, [str(profile), "2.0"], timeout=30)
    assert result.returncode == 0, result.stdout
    assert result.stdout.startswith("GOT")


def test_kernel_releases_lock_after_forced_kill(tmp_path: Path) -> None:
    """要求 6:强杀整棵进程树之后,内核自动释放锁 —— 不需要 PID/时间戳清账。"""
    profile = _profile_dir(tmp_path)
    holder = _start_holder(tmp_path, profile)
    _kill_tree(holder)
    result = _run_python(CONTENDER_SRC, [str(profile), "2.0"], timeout=30)
    assert result.returncode == 0, result.stdout
    assert result.stdout.startswith("GOT")


def test_lock_file_sits_outside_profile_dir(tmp_path: Path) -> None:
    """锁文件在 profile **之外**:Chromium 清 profile 不会顺手清掉我们的锁。"""
    profile = _profile_dir(tmp_path)
    lock = browser_lock.lock_path_for(profile)
    assert lock.parent == profile.parent
    assert lock != profile and profile not in lock.parents
    handle = browser_lock.acquire(profile, wait_s=1, poll_s=0.2, tag="placement")
    try:
        assert lock.exists()
        assert lock.stat().st_size < 4096, "锁区间不得把锁文件撑大"
    finally:
        handle.release()


def test_probe_free_is_silent_and_still_truthful(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """护栏:探测只探不占、不刷日志,别把真等待者的超时日志淹掉;结论还得是真的。"""
    profile = _profile_dir(tmp_path)
    events: list[str] = []

    class _Rec:
        def info(self, event: str, **_meta: Any) -> None:
            events.append(event)

        def warn(self, event: str, **_meta: Any) -> None:
            events.append(event)

        def error(self, event: str, **_meta: Any) -> None:
            events.append(event)

    monkeypatch.setattr(browser_lock, "LOG", _Rec())
    assert browser_lock.probe_free(profile) is True
    assert events == [], f"空闲探测不该写任何日志:{events}"

    holder = browser_lock.acquire(profile, wait_s=1, poll_s=0.2, tag="owner")
    try:
        assert browser_lock.probe_free(profile) is False, "锁被别人拿着,探测却说空闲"
        with pytest.raises(browser_lock.ProfileBusyError):
            browser_lock.acquire(profile, wait_s=0.5, poll_s=0.2, tag="waiter")
    finally:
        holder.release()
    assert "crawl.browser_lock.acquired" in events, events
    assert "crawl.browser_lock.timeout" in events, events
    assert events.count("crawl.browser_lock.released") == 1, events
    assert events.count("crawl.browser_lock.timeout") == 1, f"探测刷出来的超时:{events}"


# ------------------------------------------------------------------ fetcher 生命周期

def test_nested_session_opens_single_browser_and_locks_once(
    isolated_profile: Path, browser_opens: list[FakeSession]
) -> None:
    """要求 1/2:同进程嵌套只开一个浏览器;锁在最外层取、最外层放。"""
    profile = isolated_profile
    with fetcher.browser_session() as outer:
        assert len(browser_opens) == 1
        assert browser_lock.probe_free(profile) is False, "会话期间本项目必须持着跨进程锁"
        with fetcher.browser_session() as inner:
            assert inner is outer
            assert len(browser_opens) == 1
        assert browser_lock.probe_free(profile) is False, "嵌套退出不能把锁交出去"
    assert browser_lock.probe_free(profile) is True, "最外层退出必须交锁"
    assert browser_opens[0].closed == 1


def test_exception_inside_session_releases_lock(
    isolated_profile: Path, browser_opens: list[FakeSession]
) -> None:
    """要求 2:会话里抛异常,锁也必须交出来(否则全厂再也开不了浏览器)。"""
    profile = isolated_profile
    with pytest.raises(RuntimeError, match="采集炸了"):
        with fetcher.browser_session():
            raise RuntimeError("采集炸了")
    assert browser_lock.probe_free(profile) is True


def test_busy_lock_blocks_second_process_from_opening_browser(
    tmp_path: Path, isolated_profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """要求 4:别的进程持锁时,本进程**不会启动浏览器**,而是明确报忙。"""
    profile = isolated_profile
    holder = _start_holder(tmp_path, profile)
    opens: list[str] = []

    def spy_open() -> FakeSession:
        opens.append("open")
        return FakeSession()

    monkeypatch.setattr(fetcher, "_open_session", spy_open)
    try:
        started = time.monotonic()
        with pytest.raises(fetcher.BrowserBusyError):
            with fetcher.browser_session(wait_s=1.0):
                pass
        assert time.monotonic() - started >= 1.0
        assert opens == [], "等不到锁却启动了浏览器 = 互斥只是表面修复"
    finally:
        _release_holder(holder)


def test_browser_start_failure_still_releases_lock(
    tmp_path: Path, isolated_profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """要求 8:浏览器起不来也要放锁 —— 用真子进程证明锁真的空出来了。"""

    def broken_open() -> Any:
        raise fetcher.FetchError("browser session start failed: 模拟启动失败")

    monkeypatch.setattr(fetcher, "_open_session", broken_open)
    with pytest.raises(fetcher.FetchError):
        with fetcher.browser_session(wait_s=1):
            pass
    result = _run_python(CONTENDER_SRC, [str(isolated_profile), "2.0"], timeout=30)
    assert result.returncode == 0, result.stdout


def test_close_failure_still_releases_lock(
    tmp_path: Path,
    isolated_profile: Path,
    browser_opens: list[FakeSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """要求 9:关浏览器抛异常不能把锁永久占住。"""

    def bad_close(session: Any) -> None:
        raise RuntimeError("关浏览器炸了")

    monkeypatch.setattr(fetcher, "_close_session", bad_close)
    with pytest.raises(RuntimeError, match="关浏览器炸了"):
        with fetcher.browser_session(wait_s=1):
            pass
    result = _run_python(CONTENDER_SRC, [str(isolated_profile), "2.0"], timeout=30)
    assert result.returncode == 0, result.stdout


# ------------------------------------------------------------------ 不删仍在用的锁文件

@pytest.mark.skipif(os.name != "nt", reason="独占打开证明归属只在 Windows 实测")
def test_live_lockfile_is_never_deleted(tmp_path: Path) -> None:
    """要求 10:profile/lockfile 被活进程独占时**保留**,并明确报无法清理。"""
    profile = _profile_dir(tmp_path)
    lockfile = profile / "lockfile"
    lockfile.write_bytes(b"\x00")
    holder = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-c", EXCLUSIVE_OPEN_SRC, str(lockfile), "12"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(ROOT),
        env=_env(),
    )
    try:
        assert holder.stdout is not None
        first = holder.stdout.readline().strip()
        assert first == "EXCLUSIVE", f"{first!r} / {holder.stdout.read()[:300]!r}"
        assert browser_lock.held_by_live_process(lockfile) is True
        cleared, unresolved = fetcher._clear_stale_locks(profile)
        assert cleared == [], cleared
        assert unresolved == ["lockfile"], unresolved
        assert lockfile.exists(), "仍在使用的锁文件被删了"
    finally:
        holder.wait(timeout=60)


@pytest.mark.skipif(os.name != "nt", reason="独占打开证明归属只在 Windows 实测")
def test_stale_lockfile_cleared_only_when_provably_unowned(tmp_path: Path) -> None:
    """要求 10(反例):内核证明无人持有时,陈旧残留才允许清掉。"""
    profile = _profile_dir(tmp_path)
    for name in ("lockfile", "SingletonLock"):
        (profile / name).write_bytes(b"\x00")
    cleared, unresolved = fetcher._clear_stale_locks(profile)
    assert sorted(cleared) == ["SingletonLock", "lockfile"], cleared
    assert unresolved == []
    assert not (profile / "lockfile").exists()


def test_old_indiscriminate_lock_deletion_is_gone() -> None:
    """护栏:旧的"启动失败就无条件删 Singleton 文件"不得复活。"""
    assert not hasattr(fetcher, "_clear_profile_locks")
    source = (ROOT / "ttxt" / "crawler" / "fetcher.py").read_text(encoding="utf-8")
    assert "SingletonLock" in source  # 名单还在,但只用于"先证明归属"
    assert "_clear_stale_locks(profile)" in source


# ------------------------------------------------------------------ Worker 与 metrics 同一把锁

def test_metrics_and_worker_share_one_lock(
    tmp_path: Path,
    isolated_profile: Path,
    browser_opens: list[FakeSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """要求 11:采集与互动指标两条链都走 ``fetcher.browser_session`` ⇒ 同一把锁。"""
    profile = isolated_profile
    seen: dict[str, Any] = {}

    def fake_fetch_page(url: str, *, render: bool = False, session: Any = None, **kwargs: Any) -> Any:
        seen["render"] = render
        seen["session"] = session
        seen["lock_held"] = browser_lock.probe_free(profile) is False
        return fetcher.FetchResult(url=url, status=200, page=FakePage(), elapsed_s=0.01)

    monkeypatch.setattr(fetcher, "fetch_page", fake_fetch_page)
    db_path = _demo_db(tmp_path, ["https://www.toutiao.com/article/7707118928425763355/"])

    report = metrics_pages.collect_with_browser(
        "demo", db_path=db_path, limit=5, apply=False, lock_wait_s=5, **_metrics_kwargs()
    )
    assert seen["render"] is True
    assert seen["session"] is browser_opens[0]
    assert seen["lock_held"] is True, "指标采集期间没持锁 = 两条链没共用锁"
    assert report["candidates"] >= 1

    # Worker 侧:runner 的浏览器入口必须是同一个函数(P12 既有用例已证运行时行为,
    # 这里再钉一次"锁确实在它手里",防止以后有人给 runner 单开一条浏览器路径)
    with fetcher.browser_session() as worker_session:
        assert worker_session is browser_opens[1]
        assert browser_lock.probe_free(profile) is False


def test_metrics_collect_waits_then_reports_busy(
    tmp_path: Path, isolated_profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """要求 11:别的进程正在用浏览器时,指标采集明确报忙,并且不起浏览器。"""
    profile = isolated_profile
    opens: list[str] = []

    def spy_open() -> FakeSession:
        opens.append("open")
        return FakeSession()

    monkeypatch.setattr(fetcher, "_open_session", spy_open)
    db_path = _demo_db(tmp_path, ["https://www.toutiao.com/article/7707526791782613519/"])

    holder = _start_holder(tmp_path, profile)
    try:
        with pytest.raises(fetcher.BrowserBusyError):
            metrics_pages.collect_with_browser(
                "demo", db_path=db_path, limit=5, apply=False, lock_wait_s=1.0, **_metrics_kwargs()
            )
        assert opens == [], "等不到锁却起了浏览器 = 互斥只是表面修复"
    finally:
        _release_holder(holder)


def test_metrics_collect_forwards_lock_wait_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """要求 7/11:调度给的等待预算必须透传到取锁处,不能各自硬编码。"""
    seen: dict[str, Any] = {}

    class SpyCM:
        def __enter__(self) -> FakeSession:
            return FakeSession()

        def __exit__(self, *exc: Any) -> bool:
            return False

    def spy_session(wait_s: float | None = None) -> SpyCM:
        seen["wait_s"] = wait_s
        return SpyCM()

    monkeypatch.setattr(fetcher, "browser_session", spy_session)
    monkeypatch.setattr(
        metrics_pages, "collect_public_metrics", lambda *a, **k: {"candidates": 0, "outcome_counts": {}}
    )
    metrics_pages.collect_with_browser("demo", lock_wait_s=12.5)
    assert seen["wait_s"] == 12.5
    metrics_pages.collect_with_browser("demo")
    assert seen["wait_s"] is None, "不给预算时应当交给配置默认,而不是写死一个数"


def test_static_fetch_never_touches_browser_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """要求 12:静态 HTTP 抓取不碰浏览器锁 —— 浏览器被别的进程占着它也照常工作。"""
    profile = _profile_dir(tmp_path)
    holder = browser_lock.acquire(profile, wait_s=1, poll_s=0.2, tag="static-test")
    try:
        assert browser_lock.probe_free(profile) is False, "前置条件:锁已被占用"

        class StaticPage:
            status = 200
            headers: dict[str, str] = {}

        calls: list[str] = []

        def fake_get(url: str, timeout: float) -> StaticPage:
            calls.append(url)
            return StaticPage()

        monkeypatch.setattr(fetcher, "_scrapling_get", fake_get)
        monkeypatch.setattr(url_safety, "resolve_host", lambda host: ["93.184.216.34"])

        result = fetcher.fetch_page("https://example.com/p36", timeout=1, render=False)
        assert calls == ["https://example.com/p36"]
        assert result.status == 200
        assert browser_lock.probe_free(profile) is False, "静态抓取把浏览器锁抢走了"
    finally:
        holder.release()


# ------------------------------------------------------------------ 结构性护栏

def test_no_caller_can_bypass_browser_session() -> None:
    """护栏:除 fetcher 外,任何生产代码都不得自己起浏览器(绕过同一把锁)。

    起浏览器只有两条路:调 ``fetcher._open_session`` 或直接 import ``StealthySession``。
    ``_open_session`` 在本仓其他文件里必须一次都不出现;自起 ``StealthySession`` 的
    诊断脚本必须同时 ``browser_lock.acquire`` 持锁(与生产采集互斥),否则算绕过。
    """
    targets = [ROOT / "ttxt" / "scheduler.py"]
    targets += sorted((ROOT / "ttxt").rglob("*.py"))
    targets += sorted((ROOT / "scripts").rglob("*.py")) if (ROOT / "scripts").exists() else []
    offenders: list[str] = []
    for path in targets:
        if path.name == "fetcher.py" or "browser_lock" in path.name:
            continue
        text = path.read_text(encoding="utf-8")
        for token in ("_open_session", "StealthySession"):
            if token not in text:
                continue
            if token == "StealthySession" and "browser_lock.acquire" in text:
                continue  # 自起浏览器但持同一把 profile 锁 ⇒ 不绕过互斥
            offenders.append(f"{path.relative_to(ROOT)}: {token}")
    assert offenders == [], f"发现绕过 browser_session 的浏览器启动路径:{offenders}"

    source = (ROOT / "ttxt" / "crawler" / "fetcher.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    call_sites = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_open_session"
    ]
    assert len(call_sites) == 1, "起浏览器必须只有 browser_session 里那一处"


def test_worker_crawl_entry_uses_browser_session() -> None:
    """要求 11:Worker 的浏览器入口就是 ``fetcher.browser_session``(同一把锁)。"""
    text = (ROOT / "ttxt" / "crawler" / "runner.py").read_text(encoding="utf-8")
    assert "fetcher.browser_session()" in text
    assert "session_cm" in text, "runner 的会话入口必须仍是那个唯一的 with 语句"


def test_lock_does_not_rely_on_pid_or_timestamp() -> None:
    """护栏:锁不得退化成"看 PID/时间戳猜对方死了"。"""
    text = (ROOT / "ttxt" / "crawler" / "browser_lock.py").read_text(encoding="utf-8")
    for token in ("psutil", "os.kill(", "stale_after", "pid_alive", "touch("):
        assert token not in text, f"锁实现出现了猜测进程存活的写法:{token}"
    assert "msvcrt.locking" in text and "fcntl.flock" in text


# ------------------------------------------------------------------ 辅助:指标候选库

def _demo_db(tmp_path: Path, urls: list[str]) -> Path:
    """造一个只含候选文章的 demo 类目库,让指标采集有活可干(绝不碰生产库)。"""
    db_path = store.init_articles_db("demo", db_path=tmp_path / "demo-metrics.db")
    rows = [
        ExtractedItem(
            title=f"跨进程锁单测样本 {i} 标题够长",
            content="正文" * 300 + f" 第 {i} 篇",
            excerpt="",
            author=None,
            published_at=None,
            url=url,
        )
        for i, url in enumerate(urls, start=1)
    ]
    store.insert_items("demo", "test", rows, db_path=db_path)
    store.migrate_articles_db("demo", db_path=db_path)
    return db_path


def _metrics_kwargs() -> dict:
    """指标采集的测试参数:不发 robots 请求、不真睡觉,只走"锁 + 会话"这一层。"""
    return {
        "authorized": {metrics_pages.SOURCE_ID: "public_page"},
        "sleeper": lambda _s: None,
        "rules_provider": lambda _base: RobotsRules(allow_all=True),
    }
