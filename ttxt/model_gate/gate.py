"""串行闸门:文件锁 + FIFO 队列,任何时刻只处理一个模型任务。"""

from __future__ import annotations

import ctypes
import json
import os
import threading
import time
import uuid
from pathlib import Path

from ttxt import config as cfg
from ttxt.logging import get_logger

from . import client, queue

LOG = get_logger("model_gate")
GATE_LOCK = cfg.CONTROL_DIR / "model_gate" / "gate.lock"

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
# FILETIME 纪元(1601-01-01)与 Unix 纪元(1970-01-01)相差的 100ns 间隔数。
_FILETIME_TO_UNIX_100NS = 116444736000000000
# 同一 PID 的启动时刻容差:超过即认定旧持有者已消失、PID 被复用。
_PID_REUSE_TOLERANCE_S = 2.0
DEFAULT_TIMEOUT_S = 600.0
# 默认判死阈值必须显著小于默认等待窗口(600s),否则等待方在锁可回收前就超时。
DEFAULT_STALE_S = 180.0
DEFAULT_HEARTBEAT_S = 30.0

if os.name == "nt":  # 显式声明 Win32 原型,避免 x64 下 HANDLE 被按 int 截断。
    _KERNEL32 = ctypes.windll.kernel32
    _KERNEL32.OpenProcess.restype = ctypes.c_void_p
    _KERNEL32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
    _KERNEL32.CloseHandle.restype = ctypes.c_int
    _KERNEL32.CloseHandle.argtypes = (ctypes.c_void_p,)
    _KERNEL32.GetExitCodeProcess.restype = ctypes.c_int
    _KERNEL32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
    _KERNEL32.GetProcessTimes.restype = ctypes.c_int
    _KERNEL32.GetProcessTimes.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.POINTER(ctypes.c_ulonglong),
    )


def _inspect_process(pid: int) -> tuple[bool, float | None]:
    """返回 (进程存在且未退出, 进程启动时刻 unix 秒或 None)。仅查询句柄,不注入不杀进程。"""
    if pid <= 0:
        return False, None
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False, None
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True, None  # 无法确认 → 保守视为存活
        creation = ctypes.c_ulonglong()
        exit_time = ctypes.c_ulonglong()
        kernel_time = ctypes.c_ulonglong()
        user_time = ctypes.c_ulonglong()
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        )
        created = (creation.value - _FILETIME_TO_UNIX_100NS) / 1e7 if ok else None
        return exit_code.value == _STILL_ACTIVE, created
    finally:
        kernel32.CloseHandle(handle)


def _pid_alive(pid: int) -> bool:
    """进程存活判断;PID 复用由 _holder_alive 负责(保留本函数供只关心存活的调用方使用)。"""
    return _inspect_process(pid)[0]


def _read_lock(path: Path) -> tuple[int | None, float | None, str | None, float | None]:
    """读取锁文件 → (pid, mtime, token, holder_created)。字段缺失按 None(兼容旧格式)。"""
    try:
        text = path.read_text(encoding="ascii", errors="ignore")
    except OSError:
        return None, None, None, None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None, None, None, None
    parts = text.split()
    pid = int(parts[0]) if parts and parts[0].lstrip("-").isdigit() else None
    token = parts[2] if len(parts) > 2 else None
    created: float | None = None
    if len(parts) > 3:
        try:
            value = float(parts[3])
        except ValueError:
            value = -1.0
        created = None if value < 0 else value
    return pid, mtime, token, created


def _holder_alive(pid: int | None, recorded_created: float | None) -> tuple[bool, str]:
    """判定锁持有者是否仍可信,返回 (可信?, 原因)。

    只有"进程对象已消失 / 已退出 / PID 被复用(启动时刻不符)"才判为不可信;
    任何查询失败一律保守判活,宁可等到 stale_s 也不误删活锁。
    """
    if pid is None:
        return False, "no_pid"
    alive, actual_created = _inspect_process(pid)
    if not alive:
        return False, "process_gone_or_exited"
    if recorded_created is not None and actual_created is not None:
        if abs(actual_created - recorded_created) > _PID_REUSE_TOLERANCE_S:
            return False, "pid_reused"
    return True, "alive"


class FileLock:
    """跨进程串行闸门锁:``O_CREAT|O_EXCL`` 抢占 + 持锁续租 + 持有者身份判死。

    - 锁文件内容固定宽度:``<pid> <epoch> <token> <holder_create_s>``,便于并发读永不撕裂。
    - 只有 ``mtime`` 超过 ``stale_s`` **且** 持有者被判定不可信(进程消失/退出/PID 复用)才回收,
      因此 ``stale_s > timeout_s`` 的错误配置不会误删活锁,只会让等待方超时。
    - 持锁期间由心跳线程按 ``heartbeat_s`` 重写锁文件刷新 mtime;默认按 ``stale_s`` 自适应。
    - ``path`` 默认晚绑定 ``GATE_LOCK``,使测试可把默认锁路径重定向到临时目录。
    """

    def __init__(
        self,
        path: Path | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        stale_s: float = DEFAULT_STALE_S,
        heartbeat_s: float | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else Path(GATE_LOCK)
        self.timeout_s = float(timeout_s)
        self.stale_s = float(stale_s)
        if heartbeat_s is None:
            heartbeat_s = max(0.2, min(DEFAULT_HEARTBEAT_S, self.stale_s / 6.0))
        self.heartbeat_s = float(heartbeat_s)
        self.ownership_lost = False
        self._fd: int | None = None
        self._token: str | None = None
        self._holder_created: float | None = None
        self._hb_stop: threading.Event | None = None
        self._hb_thread: threading.Thread | None = None

    def _lock_text(self) -> str:
        """固定宽度载荷(10+1+12+1+32+1+16=73 字符),续租时整段覆写不截断。"""
        created = self._holder_created if self._holder_created is not None else -1.0
        return (
            f"{os.getpid():<10} {int(time.time()):<12} "
            f"{(self._token or ''):<32} {f'{created:.3f}':<16}"
        )

    def _write_lock(self) -> None:
        if self._fd is None:
            return
        os.lseek(self._fd, 0, os.SEEK_SET)
        os.write(self._fd, self._lock_text().encode("ascii"))

    def acquire(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.stale_s > self.timeout_s:
            LOG.warn(
                "gate.lock_stale_unreachable",
                path=str(self.path),
                stale_s=self.stale_s,
                timeout_s=self.timeout_s,
            )
        deadline = time.monotonic() + self.timeout_s
        started = time.monotonic()
        while True:
            try:
                self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                self._clear_stale()
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"model gate lock busy: {self.path}")
                time.sleep(0.2)
                continue
            self._token = uuid.uuid4().hex
            _, self._holder_created = _inspect_process(os.getpid())
            try:
                self._write_lock()
            except OSError as exc:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None
                try:
                    self.path.unlink()
                except OSError:
                    pass
                raise TimeoutError(f"model gate lock unusable: {self.path}: {exc}") from exc
            self._start_heartbeat()
            LOG.info(
                "gate.lock_acquired",
                path=str(self.path),
                pid=os.getpid(),
                waited_s=round(time.monotonic() - started, 3),
                stale_s=self.stale_s,
                heartbeat_s=self.heartbeat_s,
            )
            return self

    def _start_heartbeat(self) -> None:
        if self.heartbeat_s <= 0 or self._fd is None:
            return
        self._hb_stop = threading.Event()
        self._hb_thread = threading.Thread(target=self._heartbeat_loop, name="model-gate-heartbeat", daemon=True)
        self._hb_thread.start()

    def _heartbeat_loop(self) -> None:
        stop = self._hb_stop
        if stop is None:
            return
        while not stop.wait(self.heartbeat_s):
            if self._fd is None:
                return
            pid, _mtime, token, _created = _read_lock(self.path)
            if (token is not None and token != self._token) or (token is None and pid not in (None, os.getpid())):
                self.ownership_lost = True
                LOG.warn("gate.lock_ownership_lost", path=str(self.path), stage="heartbeat", holder=pid, holder_token=token)
                return
            try:
                self._write_lock()
            except OSError as exc:
                self.ownership_lost = True
                LOG.warn("gate.lock_heartbeat_failed", path=str(self.path), error=f"{type(exc).__name__}: {exc}")
                return

    def _clear_stale(self) -> None:
        pid, mtime, token, created = _read_lock(self.path)
        if mtime is None:
            return
        age = time.time() - mtime
        if age <= self.stale_s:
            return
        alive, reason = _holder_alive(pid, created)
        if alive:
            return
        try:
            self.path.unlink()
            LOG.warn(
                "gate.lock_stale_removed",
                path=str(self.path),
                pid=pid,
                reason=reason,
                age_s=round(age, 1),
                stale_s=self.stale_s,
                holder_created=round(created, 3) if created is not None else None,
                holder_token=token,
            )
        except FileNotFoundError:
            pass
        except OSError as exc:
            LOG.warn(
                "gate.lock_stale_unlink_failed",
                path=str(self.path),
                pid=pid,
                reason=reason,
                error=f"{type(exc).__name__}: {exc}",
            )

    def release(self) -> None:
        thread, stop = self._hb_thread, self._hb_stop
        self._hb_thread, self._hb_stop = None, None
        if stop is not None:
            stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self.heartbeat_s) + 1.0)
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError as exc:
                LOG.warn("gate.lock_close_failed", path=str(self.path), error=f"{type(exc).__name__}: {exc}")
            self._fd = None
        pid, _mtime, token, _created = _read_lock(self.path)
        mine = (token is not None and token == self._token) or (token is None and pid == os.getpid())
        if mine:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                LOG.warn("gate.lock_release_unlink_failed", path=str(self.path), error=f"{type(exc).__name__}: {exc}")
        else:
            LOG.warn("gate.lock_ownership_lost", path=str(self.path), stage="release", holder=pid, holder_token=token)

    def __enter__(self) -> "FileLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def _default_processor(row: dict) -> dict:
    payload = json.loads(row.get("payload_json") or "{}")
    prompt = payload.get("prompt") or ""
    if not prompt:
        raise client.ModelError("empty prompt")
    client.wait_ready()
    return client.chat(
        [{"role": "user", "content": prompt}],
        max_tokens=int(payload.get("max_tokens", 256)),
        temperature=float(payload.get("temperature", 0.2)),
        enable_thinking=payload.get("enable_thinking"),
    )


def _run_one(row: dict, processor, db_path: Path | None) -> dict:
    started = time.monotonic()
    LOG.info("gate.job_start", job_id=row["id"], job_type=row["job_type"])
    try:
        result = processor(row) if processor is not None else _default_processor(row)
        queue.finish(row["id"], result, db_path=db_path)
        outcome = "done"
    except Exception as exc:  # noqa: BLE001 - 失败按队列策略重试/落败
        message = f"{type(exc).__name__}: {exc}"
        outcome = queue.record_failure(row["id"], message, db_path=db_path)
        result = {"error": message}
        LOG.error("gate.job_failed", job_id=row["id"], error=message)
    LOG.info("gate.job_end", job_id=row["id"], outcome=outcome)
    return {
        "job_id": row["id"],
        "outcome": outcome,
        "result": result,
        "elapsed_s": round(time.monotonic() - started, 3),
    }


def submit_and_run(
    job_type: str,
    payload: dict,
    category: str | None = None,
    processor=None,
    db_path: Path | None = None,
    gate_lock: Path | None = None,
    wait_timeout_s: float = 600.0,
    max_jobs: int = 10,
) -> dict:
    job_id = queue.enqueue(job_type, payload, category=category, db_path=db_path)
    results: list[dict] = []
    with FileLock(gate_lock, timeout_s=wait_timeout_s):
        queue.requeue_stale_running(db_path=db_path)
        for _ in range(max_jobs):
            row = queue.claim_next(db_path=db_path)
            if row is None:
                break
            results.append(_run_one(row, processor, db_path))
            if row["id"] == job_id:
                break
    if not any(item["job_id"] == job_id for item in results):
        record = queue.get(job_id, db_path=db_path)
        results.append(
            {
                "job_id": job_id,
                "outcome": (record or {}).get("status", "unknown"),
                "result": {"note": "job handled by another process", "record": record},
                "elapsed_s": 0.0,
            }
        )
    return {"job_id": job_id, "results": results}


def drain(
    processor=None,
    db_path: Path | None = None,
    gate_lock: Path | None = None,
    wait_timeout_s: float = 600.0,
    max_jobs: int = 50,
) -> dict:
    results: list[dict] = []
    with FileLock(gate_lock, timeout_s=wait_timeout_s):
        queue.requeue_stale_running(db_path=db_path)
        for _ in range(max_jobs):
            row = queue.claim_next(db_path=db_path)
            if row is None:
                break
            results.append(_run_one(row, processor, db_path))
    return {"processed": results}
