"""P12 受限采集 Worker:唯一持续消费 ``crawl_jobs`` 的执行器。

硬边界(验收对照):
- 只认领 ``job_type='crawl'``;本模块不导入任何模型通道(``ttxt.model*``),
  Worker 进程在代码层面就不可能触发 Strata 调用;写作/发布代码路径不存在。
- 单实例:``00_control/worker.lock`` 字节区域锁。第二个实例启动即抛
  ``WorkerAlreadyRunning``,防止两个浏览器互相争抢、并发写同名队列。
- 停止:``00_control/worker_stop`` 存在则本轮边界优雅退出;``--max-jobs`` /
  ``--max-runtime-s`` / 空闲超时同样在边界退出。退出只释放锁,绝不伪造收尾:
  被强杀留下的 ``running`` 由下次启动的 recover 通道(租约过期)处理。
- 断点续采/崩溃恢复:启动先 ``recover_stale_crawl_jobs``(租约过期→failed)
  再 ``requeue_failed_crawl_jobs``(attempts 未达上限→重新排队),队列不会卡死。
- 同源不并发、``attempts``/``max_attempts`` 封顶、浏览器页池默认小值,均由
  ``store.claim_next_crawl_job`` 与 ``fetcher`` 层强制,Worker 无法绕过。
"""

from __future__ import annotations

import msvcrt
import os
import time
from pathlib import Path
from typing import Any, TextIO

from ttxt import config as cfg
from ttxt.logging import get_logger

from . import runner, store

LOG = get_logger("crawler.worker")

STOP_FILENAME = "worker_stop"
LOCK_FILENAME = "worker.lock"
DEFAULT_LEASE_S = 900.0


class WorkerAlreadyRunning(RuntimeError):
    """已有 Worker 持有 worker.lock(或锁残留未释放)。"""


def stop_path() -> Path:
    return cfg.CONTROL_DIR / STOP_FILENAME


def request_stop() -> Path:
    """人工/面板下发的软停:创建停止标志,Worker 在下个边界退出。"""
    path = stop_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def _acquire_lock() -> TextIO | None:
    """独占持有 worker.lock;拿不到返回 ``None``。进程死亡时 OS 自动释放。"""
    lock_path = cfg.CONTROL_DIR / LOCK_FILENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle: TextIO = open(lock_path, "a+", encoding="utf-8")
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
    return handle


def _release_lock(handle: TextIO) -> None:
    try:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    handle.close()


def run_worker(
    owner: str,
    max_jobs: int = 0,
    max_runtime_s: float | None = None,
    idle_stop_s: float = 120.0,
    category: str | None = None,
    lease_s: float = DEFAULT_LEASE_S,
    poll_s: float = 1.0,
) -> dict[str, Any]:
    """受限消费循环。返回诚实的执行小结(不夸大:``done`` 只数实际认领执行的任务)。

    - ``max_jobs``:认领并执行 N 个任务后退出(0 = 不限)。
    - ``max_runtime_s``:运行预算,到点在边界退出(0 = 立即在边界退出)。
    - ``idle_stop_s``:队列连续空闲该秒数后退出(0 = 永不因空闲退出)。
    """
    lock = _acquire_lock()
    if lock is None:
        raise WorkerAlreadyRunning("已有 Worker 持有 worker.lock,拒绝第二个实例并发跑浏览器")
    try:
        # 崩溃恢复先行:上次被强杀留下的 running 若租约过期 → failed → 未达上限重新排队
        recovered = store.recover_stale_crawl_jobs(lease_s=lease_s)
        requeued = store.requeue_failed_crawl_jobs()
        if recovered or requeued:
            LOG.info("worker.recover", owner=owner, recovered=recovered, requeued=requeued)
        started_at = time.monotonic()
        last_job_at = started_at
        done = 0
        results: list[dict[str, Any]] = []
        stop_reason = "unknown"
        while True:
            if stop_path().exists():
                stop_reason = "stop_file"
                break
            if max_runtime_s is not None and time.monotonic() - started_at >= max_runtime_s:
                stop_reason = "max_runtime"
                break
            summary = runner.run_next(owner=owner, category=category, lease_s=lease_s)
            if summary is None:
                if idle_stop_s and (time.monotonic() - last_job_at) >= idle_stop_s:
                    stop_reason = "idle"
                    break
                time.sleep(poll_s)
                continue
            done += 1
            last_job_at = time.monotonic()
            results.append(summary)
            LOG.info(
                "worker.job_done",
                owner=owner,
                job_id=summary.get("job_id"),
                source=summary.get("source"),
                ok=summary.get("ok"),
            )
            if max_jobs and done >= max_jobs:
                stop_reason = "max_jobs"
                break
        LOG.info("worker.stop", owner=owner, reason=stop_reason, done=done)
        return {
            "owner": owner,
            "done": done,
            "recovered": recovered,
            "requeued": requeued,
            "stop_reason": stop_reason,
            "elapsed_s": round(time.monotonic() - started_at, 2),
            "results": results,
        }
    finally:
        _release_lock(lock)
