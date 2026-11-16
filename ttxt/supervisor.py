"""P31 进程托管:让已有的 ``ttxt schedule --daemon`` 与 ``ttxt worker`` 真的常驻跑着。

为什么需要这个模块(2026-11-16 本机实测):
- 调度器与 Worker 的实现都在,但仓库里只有面板的启动入口(``start-dashboard.cmd`` / ``.vbs``),
  没有任何进程托管入口;
- ``00_control/scheduler_state.json`` 最后执行停在 ``2026-11-12T19:57:35+08:00``,停摆约 94 小时;
- Windows 计划任务里没有任何 ttxt 条目,机器重启或关掉窗口后没人把常驻进程拉回来。

边界(一条都不放宽):
- 只托管 ``ttxt schedule --daemon`` 与 ``ttxt worker`` 两类子进程;``write`` / ``publish`` /
  ``model`` / ``model_drain`` / ``draft`` / ``serve`` 一律禁止托管(与 ``scheduler.FORBIDDEN_KINDS``
  同一立场:写作、发布、模型调用不能由自动进程触发)。
- 单实例:``00_control/supervisor.lock`` 用 ``msvcrt`` 区域锁,和 ``worker.lock`` 同一手法,
  进程死亡时 OS 自动释放,不会留下"看着像有人在跑"的假锁;``--ensure`` 只有在锁空闲时才拉起新常驻进程。
- 退出状态必须留痕:``00_control/supervisor_state.json`` 记录每个子进程的 pid 列表、启动时间、
  退出码、重启次数、放弃原因;软停时写 ``stopped_at``。
- 软停:``00_control/supervisor_stop`` 存在 → 终止子进程后退出,绝不伪造收尾。
  Worker 自己的 ``worker_stop`` 标志被创建时,托管**不会**违背运维意图反复重启 Worker,
  而是标记 ``blocked_by_stop_flag`` 并放弃,等人 ``worker --clear-stop`` 后重新 ``supervisor --ensure``。
"""

from __future__ import annotations

import ctypes
import json
import msvcrt
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, TextIO

from ttxt import config as cfg
from ttxt.logging import get_logger

LOG = get_logger("supervisor")

LOCK_FILENAME = "supervisor.lock"
STOP_FILENAME = "supervisor_stop"
STATE_FILENAME = "supervisor_state.json"

ALLOWED_CHILD_KINDS = {"schedule", "worker"}
FORBIDDEN_CHILD_KINDS = {"write", "publish", "model", "model_drain", "draft", "serve"}

DEFAULT_RESTART_DELAY_S = 5.0
DEFAULT_MAX_RESTARTS = 10
DEFAULT_WINDOW_S = 600.0
DEFAULT_POLL_S = 1.0
DEFAULT_HEARTBEAT_S = 10.0
DEFAULT_SCHEDULER_INTERVAL_S = 300
WORKER_OWNER = "supervisor"

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0


class SupervisorAlreadyRunning(RuntimeError):
    """已有常驻托管进程持有 ``supervisor.lock``(单实例约束)。"""


_KERNEL32 = None


def _kernel32():
    """带正确原型的 kernel32(64 位下句柄必须按 c_void_p 取回,否则会被截断)。"""
    global _KERNEL32
    if _KERNEL32 is None:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        kernel32.WaitForSingleObject.restype = ctypes.c_ulong
        kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        _KERNEL32 = kernel32
    return _KERNEL32


def default_lock_path() -> Path:
    return cfg.CONTROL_DIR / LOCK_FILENAME


def default_stop_path() -> Path:
    return cfg.CONTROL_DIR / STOP_FILENAME


def default_state_path() -> Path:
    return cfg.CONTROL_DIR / STATE_FILENAME


def policy(conf: dict[str, Any] | None = None) -> dict[str, Any]:
    """托管参数:config.json 的 ``supervisor`` 段覆盖模块默认值(唯一口径来源)。"""
    if conf is None:
        conf = cfg.load_config()
    block = conf.get("supervisor", {}) or {}
    merged = {
        "scheduler_interval_s": DEFAULT_SCHEDULER_INTERVAL_S,
        "worker_owner": WORKER_OWNER,
        "restart_delay_s": DEFAULT_RESTART_DELAY_S,
        "max_restarts": DEFAULT_MAX_RESTARTS,
        "window_s": DEFAULT_WINDOW_S,
        "poll_s": DEFAULT_POLL_S,
        "heartbeat_s": DEFAULT_HEARTBEAT_S,
        "heartbeat_fresh_s": 900.0,
    }
    merged.update({key: value for key, value in block.items() if key in merged})
    merged["scheduler_interval_s"] = int(float(merged["scheduler_interval_s"]))
    merged["worker_owner"] = str(merged["worker_owner"])
    for key in ("restart_delay_s", "window_s", "poll_s", "heartbeat_s", "heartbeat_fresh_s"):
        merged[key] = float(merged[key])
    merged["max_restarts"] = int(float(merged["max_restarts"]))
    return merged


def _resolve(path: str | Path | None, default_fn: Callable[[], Path]) -> Path:
    return Path(path) if path else default_fn()


def pid_path_for(lock_path: str | Path | None = None) -> Path:
    """PID 单独放一个文件:锁文件被区域锁占住的字节区读不了(实测 PermissionError)。"""
    lock = _resolve(lock_path, default_lock_path)
    return lock.with_name(lock.name.replace(".lock", ".pid"))


# --------------------------------------------------------------------------- 锁与存活


def acquire_lock(lock_path: str | Path | None = None) -> TextIO | None:
    """独占持有 ``supervisor.lock``;拿不到返回 ``None``(进程死亡时 OS 自动释放)。"""
    path = _resolve(lock_path, default_lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle: TextIO = open(path, "a+", encoding="utf-8")
    try:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    # 锁文件里的字节别人读不到,所以 PID 另外落一份可读的侧文件。
    try:
        pid_path_for(path).write_text(str(os.getpid()), encoding="utf-8")
    except OSError:  # pragma: no cover - 侧文件写失败不影响锁本身
        LOG.warn("lock.pid_write_failed", "写入 PID 侧文件失败", lock=str(path))
    return handle


def release_lock(handle: TextIO) -> None:
    try:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    handle.close()
    try:
        pid_path = pid_path_for(Path(handle.name))
        if read_pid_at(pid_path) == os.getpid():
            pid_path.unlink()
    except OSError:  # pragma: no cover
        pass


def read_pid_at(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def read_lock_pid(lock_path: str | Path | None = None) -> int | None:
    return read_pid_at(pid_path_for(lock_path))


def pid_alive(pid: int | None) -> bool:
    """用 Win32 句柄判断 PID 是否还活着(不依赖外部命令,也不看锁文件名字)。"""
    if not isinstance(pid, int) or pid <= 0:
        return False
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(_SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        # 句柄已被 signaled(返回 0)说明进程已退出,包括还没被回收的子进程僵尸。
        return kernel32.WaitForSingleObject(handle, 0) != _WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)


def _heartbeat_age_s(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return (datetime.now().astimezone() - parsed.astimezone()).total_seconds()


def supervisor_running(lock_path: str | Path | None = None,
                      state_path: str | Path | None = None,
                      *,
                      stale_after_s: float = 180.0) -> tuple[bool, dict[str, Any]]:
    lock = _resolve(lock_path, default_lock_path)
    state = read_state(state_path)
    pid = read_lock_pid(lock)
    age = _heartbeat_age_s(state.get("updated_at"))
    info: dict[str, Any] = {
        "pid": pid,
        "lock": str(lock),
        "state_updated_at": state.get("updated_at"),
        "heartbeat_age_s": age,
        "stale_heartbeat": bool(age is not None and age > stale_after_s),
        "children": state.get("children", []),
    }
    if pid is not None and pid_alive(pid):
        return True, info
    info["stale_lock"] = pid is not None
    return False, info


# --------------------------------------------------------------------------- 状态文件


def read_state(state_path: str | Path | None = None) -> dict[str, Any]:
    path = _resolve(state_path, default_state_path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(state: dict[str, Any], state_path: str | Path | None = None) -> None:
    path = _resolve(state_path, default_state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def request_stop(stop_path: str | Path | None = None) -> Path:
    path = _resolve(stop_path, default_stop_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def clear_stop(stop_path: str | Path | None = None) -> bool:
    path = _resolve(stop_path, default_stop_path)
    try:
        path.unlink()
    except OSError:
        return False
    return True


# --------------------------------------------------------------------------- 子进程清单


def build_child_commands(python_exec: str | Path | None = None,
                         scheduler_interval_s: int = DEFAULT_SCHEDULER_INTERVAL_S,
                         worker_owner: str = WORKER_OWNER,
                         extra_argv: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """构造要托管的两条命令。禁止把写作/发布/模型/面板塞进托管清单。"""
    extra = [str(a) for a in (extra_argv or [])]
    bad = [a for a in extra if a in FORBIDDEN_CHILD_KINDS]
    if bad:
        raise ValueError(f"托管禁止启动这些子命令:{bad}(只允许 {sorted(ALLOWED_CHILD_KINDS)})")
    py = str(python_exec or sys.executable)
    base = [py, "-X", "utf8", "-m", "ttxt"]
    return [
        {
            "name": "scheduler",
            "kind": "schedule",
            "argv": base + ["schedule", "--daemon", "--interval-sec", str(int(scheduler_interval_s))],
        },
        {
            "name": "worker",
            "kind": "worker",
            "argv": base + ["worker", "--owner", str(worker_owner),
                            "--max-jobs", "0", "--idle-stop-s", "0"],
        },
    ]


def _worker_stop_requested() -> bool:
    """Worker 自己的软停标志存在时,托管不得违背运维意图反复重启它。"""
    try:
        from ttxt.crawler import worker as crawler_worker
    except Exception:  # pragma: no cover - 导入失败按"没有停"处理
        return False
    return crawler_worker.stop_path().exists()


def _public(child: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in child.items() if k not in ("proc", "restart_history")}


def supervise(children: list[dict[str, Any]] | None = None,
              *,
              python_exec: str | Path | None = None,
              scheduler_interval_s: int = DEFAULT_SCHEDULER_INTERVAL_S,
              worker_owner: str = WORKER_OWNER,
              restart_delay_s: float = DEFAULT_RESTART_DELAY_S,
              max_restarts: int = DEFAULT_MAX_RESTARTS,
              window_s: float = DEFAULT_WINDOW_S,
              poll_s: float = DEFAULT_POLL_S,
              heartbeat_s: float = DEFAULT_HEARTBEAT_S,
              run_for_s: float | None = None,
              terminate_timeout_s: float = 12.0,
              lock_path: str | Path | None = None,
              state_path: str | Path | None = None,
              stop_path: str | Path | None = None) -> dict[str, Any]:
    """常驻看护:子进程掉了就重启,超限就放弃并把原因写进状态文件。"""
    state_file = _resolve(state_path, default_state_path)
    stop_file = _resolve(stop_path, default_stop_path)
    if children is None:
        children = build_child_commands(python_exec=python_exec,
                                        scheduler_interval_s=scheduler_interval_s,
                                        worker_owner=worker_owner)
    tracked: list[dict[str, Any]] = []
    for spec in children:
        tracked.append({
            "name": str(spec["name"]),
            "kind": str(spec.get("kind", spec["name"])),
            "argv": [str(a) for a in spec["argv"]],
            "proc": None,
            "pids": [],
            "runs": 0,
            "restarts": 0,
            "exit_code": None,
            "started_at": None,
            "exited_at": None,
            "giving_up": False,
            "give_up_reason": None,
            "terminated": False,
            "killed": False,
            "restart_history": [],
        })

    stop_reason: str | None = None
    started = time.time()
    deadline = (started + float(run_for_s)) if run_for_s else None
    last_beat = 0.0

    def persist(extra: dict[str, Any] | None = None) -> dict[str, Any]:
        state: dict[str, Any] = {
            "supervisor_pid": os.getpid(),
            "updated_at": cfg.now_iso(),
            "children": [_public(t) for t in tracked],
        }
        if stop_reason:
            state["stop_reason"] = stop_reason
        if extra:
            state.update(extra)
        write_state(state, state_file)
        return state

    def start(child: dict[str, Any]) -> None:
        # 显式 cwd=项目根:子进程是 `-m ttxt ...`,没有项目根就找不到包(实测计划任务默认
        # 工作目录是 C:\Windows\System32,子进程会立刻退出,托管看起来"起来了"其实什么都没跑)。
        proc = subprocess.Popen(child["argv"], cwd=str(cfg.PROJECT_ROOT))
        child["proc"] = proc
        child["runs"] += 1
        child["pids"].append(int(proc.pid))
        child["started_at"] = cfg.now_iso()
        child["exit_code"] = None
        child["terminated"] = False
        LOG.info("child.start", f"托管启动 {child['name']} pid={proc.pid}(第 {child['runs']} 次)",
                 name=child["name"], pid=int(proc.pid), runs=child["runs"])

    for child in tracked:
        start(child)
    persist()

    while True:
        now = time.time()
        if stop_file.exists():
            stop_reason = "stop_flag"
            break
        if deadline is not None and now >= deadline:
            stop_reason = "run_for_s"
            break
        dirty = False
        for child in tracked:
            if child["giving_up"] or child["proc"] is None:
                continue
            code = child["proc"].poll()
            if code is None:
                continue
            dirty = True
            child["exit_code"] = int(code)
            child["exited_at"] = cfg.now_iso()
            child["proc"] = None
            LOG.warn("child.exit", f"托管子进程 {child['name']} 退出,退出码={code}",
                     name=child["name"], exit_code=int(code), runs=child["runs"])
            if child["kind"] == "worker" and _worker_stop_requested():
                child["giving_up"] = True
                child["give_up_reason"] = "blocked_by_stop_flag"
                LOG.warn("child.worker_stop_flag",
                         "Worker 的 worker_stop 标志存在,托管不重启(等人 worker --clear-stop 后再 ensure)",
                         name=child["name"])
                continue
            recent = [ts for ts in child["restart_history"] if now - ts <= window_s]
            child["restart_history"] = recent
            if len(recent) >= int(max_restarts):
                child["giving_up"] = True
                child["give_up_reason"] = f"restarts_exceeded({max_restarts}/{int(window_s)}s)"
                LOG.error("child.give_up",
                          f"托管子进程 {child['name']} 在 {int(window_s)}s 内重启 {len(recent)} 次,放弃自动恢复",
                          name=child["name"], window_s=int(window_s), restarts=len(recent))
                continue
            time.sleep(max(0.0, float(restart_delay_s)))
            child["restarts"] += 1
            child["restart_history"] = recent + [time.time()]
            if not stop_file.exists():
                start(child)
        if dirty or (heartbeat_s and now - last_beat >= heartbeat_s):
            persist()
            last_beat = now
        if all(t["giving_up"] for t in tracked):
            stop_reason = "all_children_given_up"
            break
        time.sleep(max(0.02, float(poll_s)))

    for child in tracked:
        proc = child["proc"]
        if proc is None:
            continue
        try:
            proc.terminate()
            child["terminated"] = True
        except OSError as exc:  # pragma: no cover - terminate 失败极少见
            LOG.error("child.terminate_failed", f"终止 {child['name']} 失败:{exc}",
                      name=child["name"], error=str(exc))
    term_deadline = time.time() + max(0.2, float(terminate_timeout_s))
    for child in tracked:
        proc = child["proc"]
        if proc is None:
            continue
        try:
            code = proc.wait(timeout=max(0.1, term_deadline - time.time()))
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                code = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover
                code = None
            child["killed"] = True
        child["exit_code"] = code
        child["exited_at"] = cfg.now_iso()
        child["proc"] = None
    persist({"stopped_at": cfg.now_iso()})
    return {
        "stop_reason": stop_reason or "unknown",
        "children": [_public(t) for t in tracked],
        "giving_up": [t["name"] for t in tracked if t["giving_up"]],
        "state_path": str(state_file),
    }


def run_forever(children: list[dict[str, Any]] | None = None,
                *,
                lock_path: str | Path | None = None,
                **kwargs: Any) -> dict[str, Any]:
    """持锁常驻。已有托管进程在跑时抛 ``SupervisorAlreadyRunning``,绝不出现两份。"""
    lock = _resolve(lock_path, default_lock_path)
    handle = acquire_lock(lock)
    if handle is None:
        raise SupervisorAlreadyRunning(f"已有托管进程持有 {lock}(单实例约束)")
    try:
        return supervise(children=children, lock_path=lock, **kwargs)
    finally:
        release_lock(handle)


def default_launcher(argv: list[str]) -> int:
    """无窗口拉起常驻托管进程,标准输出/错误落到 ``09_logs``。"""
    log_dir = cfg.LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    out = open(log_dir / "supervisor.out.log", "ab")
    err = open(log_dir / "supervisor.err.log", "ab")
    proc = subprocess.Popen(argv, cwd=str(cfg.PROJECT_ROOT), stdout=out, stderr=err,
                            stdin=subprocess.DEVNULL,
                            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                            close_fds=True)
    return int(proc.pid)


def ensure(launcher: Callable[[list[str]], int] | None = None,
           run_argv: list[str] | None = None,
           lock_path: str | Path | None = None,
           state_path: str | Path | None = None,
           stop_path: str | Path | None = None) -> dict[str, Any]:
    """幂等保障:没有在跑就拉起一份,已经在跑就什么都不做(计划任务可以放心反复调)。"""
    alive, info = supervisor_running(lock_path, state_path)
    if alive:
        return {"started": False, "reason": "already-running", "pid": info["pid"]}
    stop_file = _resolve(stop_path, default_stop_path)
    if stop_file.exists():
        return {"started": False, "reason": "stop-flag-present", "stop_flag": str(stop_file)}
    argv = list(run_argv or [sys.executable, "-X", "utf8", "-m", "ttxt", "supervisor", "--run"])
    launch = launcher or default_launcher
    pid = launch(argv)
    LOG.info("supervisor.ensure", f"托管进程已拉起 pid={pid}", pid=int(pid), argv=argv)
    return {"started": True, "pid": int(pid), "argv": argv}
