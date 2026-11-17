"""浏览器 Profile 的**跨进程**互斥锁(P36,2026-11-17)。

为什么单独一个模块,而不是塞进 ``fetcher.py``:
这把锁只依赖操作系统语义(msvcrt / fcntl),必须能在**不导入 Scrapling、不起浏览器**
的前提下被子进程导入并测试 —— P36 的验收要求就是"两个真进程抢同一把锁,最多一个拿到"。
塞进 fetcher.py 就等于让每个多进程测试用例都付一次浏览器库的导入代价,也无法单独验证。

为什么必须用操作系统的锁,而不是 PID 文件 / 时间戳 / ``threading.Lock``:
- ``threading.Lock`` 只在**本进程**内有效,而真实的冲突恰恰是两个进程(采集 Worker 与
  调度进程)共用同一份 ``02_crawler/.browser/profile``;
- PID 文件与时间戳都要靠"猜对方是不是死了",进程崩溃后必然留下假锁或假可占用。
字节区间锁(``msvcrt.locking``)/ ``flock`` 由内核记账:进程一死(正常退出、被强杀、
崩溃)句柄关闭,锁**自动**消失,没有任何"陈旧锁"需要清理。这是 P36 能安全落地的根基。

实测依据(2026-11-17,``07_tmp/p36/probe_*.py``,独立临时 profile):
- 浏览器运行时 profile 里只有 ``lockfile`` 一个锁文件,且被活进程独占到**连读都读不开**
  (PermissionError);没有 ``SingletonLock``/``SingletonCookie``/``SingletonSocket``;
- 强杀整棵进程树后 profile 里**一个锁文件都不剩**,不删任何文件直接重开 **1.8 秒**成功;
- 第二个进程对同一个 profile 开会话 **1.9 秒**失败,报的是
  ``TargetClosedError: ... browser has been closed``(完全看不出根因)。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from ttxt.logging import get_logger

LOG = get_logger("browser_lock")

#: 锁文件名。刻意放在 profile 目录**外面**(``.browser/profile.lock``):
#: Chromium 清理/重置 profile 时不会碰到它,我们的清理也不会碰它的内部文件。
LOCK_FILE_NAME = "profile.lock"

#: 默认等待上限:采集 Worker 用。metrics 一轮实测约 3.5 分钟,Worker 等得起;
#: 等过 10 分钟说明对手不是正常轮次,交给调用方按可重试失败处理。
DEFAULT_WAIT_S = 600.0
DEFAULT_POLL_S = 2.0

_LOCK_BYTES = 1  # 只锁 1 字节:所有持锁方锁同一段区间 ⇒ 必然互斥
# 锁区间刻意放到 1 MB 处,**远离**写在文件开头的诊断信息:Windows 的字节区间锁会连
# 被锁区间的"读"一起挡,锁在 0 字节就等于让所有人读不出"谁持着锁"(实测如此)。
# 锁在 EOF 之外的区间不影响文件长度,也不影响读写前面的诊断内容。
_LOCK_OFFSET = 1_048_576


class ProfileBusyError(RuntimeError):
    """等待超时:profile 被**另一个进程**占着。调用方据此让路,而不是当抓取失败。"""

    def __init__(self, message: str, *, lock_path: Path, waited_s: float, holder: dict[str, Any] | None = None):
        super().__init__(message)
        self.lock_path = Path(lock_path)
        self.waited_s = float(waited_s)
        self.holder = holder or {}


def _profile_dir() -> Path:
    """默认 profile 路径(与 ``fetcher.browser_profile_dir()`` 同一个表达式)。

    这里不 import fetcher:fetcher 依赖本模块,反向导入会成环。生产路径一律由
    ``fetcher.browser_session()`` 显式传入自己的 ``browser_profile_dir()``,
    本函数只服务于独立探针(如体检、测试)。
    """
    from ttxt import config as cfg

    return cfg.CRAWLER_DIR / ".browser" / "profile"


def lock_path_for(profile_dir: Path | str | None = None) -> Path:
    """给定 profile 推出锁文件路径(在 profile 目录之外)。"""
    profile = Path(profile_dir) if profile_dir is not None else _profile_dir()
    return profile.parent / LOCK_FILE_NAME


def lock_path() -> Path:
    """默认 profile 的锁文件路径(体检/探针用)。"""
    return lock_path_for(_profile_dir())


def _try_lock(fh: Any) -> bool:
    """非阻塞尝试持锁;拿到 True,被别人占着 False。"""
    if os.name == "nt":
        import msvcrt

        try:
            fh.seek(_LOCK_OFFSET)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, _LOCK_BYTES)
            return True
        except OSError:
            return False
    try:
        import fcntl

        fh.seek(_LOCK_OFFSET)
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False
    except ImportError:  # pragma: no cover - 非 Windows 且无 fcntl
        raise RuntimeError(f"当前平台({os.name})没有可用的跨进程文件锁实现,拒绝假装加锁") from None


def _unlock(fh: Any) -> None:
    if os.name == "nt":
        import msvcrt

        fh.seek(_LOCK_OFFSET)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, _LOCK_BYTES)
        return
    import fcntl

    fh.seek(_LOCK_OFFSET)
    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class ProfileHandle:
    """一次持锁的凭据。释放靠 close,进程死亡时由内核自动释放(不靠我们清账)。"""

    def __init__(self, path: Path, fh: Any, *, tag: str, waited_s: float, quiet: bool = False):
        self.path = path
        self.tag = tag
        self.waited_s = float(waited_s)
        self._quiet = bool(quiet)
        self._fh = fh
        self.acquired_at = time.time()

    @property
    def held(self) -> bool:
        return self._fh is not None

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            _unlock(self._fh)
        except OSError as exc:  # noqa: BLE001 - 解不开也必须关掉句柄(关掉即内核释放)
            LOG.warn("crawl.browser_lock.unlock_error", path=str(self.path), error=str(exc)[:160])
        try:
            self._fh.close()
        except OSError:
            pass
        self._fh = None
        if not self._quiet:
            LOG.info("crawl.browser_lock.released", path=str(self.path), tag=self.tag, held_s=round(time.time() - self.acquired_at, 1))

    def __enter__(self) -> "ProfileHandle":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.release()


def holder_info(path: Path | str | None = None) -> dict[str, Any]:
    """尽力读出"上一个持锁者留下的诊断信息"(纯诊断,不参与任何判定,读不到就算了)。"""
    target = Path(path) if path is not None else lock_path()
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as fh:
            data = json.loads(fh.read() or "{}")
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _open_handle(path: Path) -> Any:
    """打开锁文件句柄:**绝不清空**已有内容(那是别人的诊断信息)。

    不能用 ``a+b``:追加模式下 seek 对写入无效,诊断信息会变成一串拼接的 JSON。
    先 ``r+b``,不存在再 ``x+b`` 独占创建(并发创建谁赢都无所谓,内容只是诊断)。
    """
    try:
        return open(path, "r+b")  # noqa: SIM115 - 句柄交给 ProfileHandle 管生命周期
    except FileNotFoundError:
        try:
            return open(path, "x+b")  # noqa: SIM115
        except FileExistsError:
            return open(path, "r+b")  # noqa: SIM115


def acquire(
    profile_dir: Path | str | None = None,
    *,
    wait_s: float | None = None,
    poll_s: float | None = None,
    tag: str = "",
    quiet: bool = False,
) -> ProfileHandle:
    """阻塞取锁,最长 ``wait_s``;超时抛 ``ProfileBusyError``(明确状态,不是假成功)。

    ``quiet=True`` 只用于探测(``probe_free``):探测每秒都可能做,把"没拿到"写成 warn
    会把日志刷满,真实等待者的一条 warn 就被淹掉了。探测的结论由返回值表达。
    """
    path = lock_path_for(profile_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    wait = DEFAULT_WAIT_S if wait_s is None else max(0.0, float(wait_s))
    poll = DEFAULT_POLL_S if poll_s is None else max(0.05, float(poll_s))

    fh = _open_handle(path)
    started = time.monotonic()
    try:
        while True:
            if _try_lock(fh):
                waited = time.monotonic() - started
                _write_holder(fh, tag)
                if not quiet:
                    LOG.info("crawl.browser_lock.acquired", path=str(path), tag=tag, waited_s=round(waited, 1))
                return ProfileHandle(path, fh, tag=tag, waited_s=waited, quiet=quiet)
            left = wait - (time.monotonic() - started)
            if left <= 0:
                break
            time.sleep(min(poll, left))
        waited = time.monotonic() - started
    except BaseException:
        try:
            fh.close()
        except OSError:
            pass
        raise
    waited = time.monotonic() - started
    try:
        fh.close()
    except OSError:
        pass
    holder = holder_info(path)
    if not quiet:
        LOG.warn(
            "crawl.browser_lock.timeout",
            path=str(path),
            waited_s=round(waited, 1),
            holder_pid=holder.get("pid"),
            holder_tag=holder.get("tag"),
        )
    raise ProfileBusyError(
        f"浏览器 profile 锁等待 {waited:.0f} 秒仍被占用:{path}"
        + (f"(当前持锁:pid={holder.get('pid')} tag={holder.get('tag')!r})" if holder else ""),
        lock_path=path,
        waited_s=waited,
        holder=holder,
    )


def _write_holder(fh: Any, tag: str) -> None:
    """写"谁持着锁"的诊断信息。只在持锁后写,所以并发下不会互相踩踏。"""
    try:
        payload = json.dumps(
            {"pid": os.getpid(), "tag": tag, "acquired_at": time.time(), "host": os.environ.get("COMPUTERNAME", "")},
            ensure_ascii=False,
        )
        fh.seek(0)
        fh.write(payload.encode("utf-8"))
        fh.truncate()
        fh.flush()
    except OSError as exc:  # noqa: BLE001 - 诊断信息写不出不影响加锁本身
        LOG.warn("crawl.browser_lock.holder_info_error", error=str(exc)[:160])


def probe_free(profile_dir: Path | str | None = None) -> bool:
    """只探不占:能瞬间拿到又马上放回 ⇒ True(用于让路判断与体检,不是安全边界)。"""
    try:
        handle = acquire(profile_dir, wait_s=0.0, poll_s=0.05, tag="probe", quiet=True)
    except ProfileBusyError:
        return False
    handle.release()
    return True


def held_by_live_process(path: Path | str) -> bool:
    """用操作系统给的事实判断一个文件是否被**活进程独占**:能不能独占打开。

    实测(Windows + Edge/Chromium):浏览器运行时的 ``profile/lockfile`` 被它独占,
    ``open(..., 'r+b')`` 直接 PermissionError;没有进程持有时能打开。
    这不是"看时间戳猜",是内核的共享冲突判定。非 Windows 上无法用同一手段证明,
    因此**返回不了结论**(调用方必须按"无法确认"处理,不得删文件)。
    """
    if os.name != "nt":
        raise RuntimeError("非 Windows 平台无法用独占打开证明锁文件归属,拒绝猜测")
    try:
        with open(path, "r+b"):
            return False
    except PermissionError:
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:  # noqa: BLE001 - 共享冲突之外的错误一律按"无法确认"
        raise RuntimeError(f"无法确认锁文件归属:{exc}") from exc
