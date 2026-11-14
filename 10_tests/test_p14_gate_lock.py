"""P14 测试:model_gate 闸门锁的 timeout/stale、PID 复用、续租与跨进程互斥(C-7/C-8)。

覆盖矩阵(与 08_wiki/reports/P13-fix-C7C8-20261114.md §5 对应):
① stale_s > timeout_s 时不会错误回收活锁
② PID 复用能被识别;启动时刻一致的活持有者不会被误判
③ 持锁方能续租,空闲超过 stale_s 也不会被回收
④ 两个独立进程竞争同一把锁,同一时刻只有一个持有(或只有一个获得)
⑤ 测试默认锁路径落在临时目录,生产 00_control/model_gate/gate.lock 不出现
⑥ 测试只用临时 system.db,不打开生产 system.db
⑦ 测试期间与结束后,生产数据库与控制文件逐字节不变(由 conftest 会话夹具把关)
⑧ 全量 pytest 回归(见报告中的真实命令与计数)

所有锁路径都在 pytest 临时目录内,绝不使用 D:/ttxt/00_control/model_gate。
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ttxt import config as cfg
from ttxt import db as system_db
from ttxt.model_gate import gate, queue


def sidecars(db_path: Path) -> list[str]:
    """返回生产库目录里的 WAL 边车名;只读连接同样会生成它们,因此用于“未连接”取证。"""
    return sorted(
        item.name
        for item in db_path.parent.glob(db_path.name + "*")
        if item.name.endswith(("-wal", "-shm", "-journal"))
    )

_RUNNER = r'''
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\ttxt")
from ttxt.model_gate import gate  # noqa: E402

out, lock_path, hold_s, timeout_s, stale_s, heartbeat_s = sys.argv[1:7]
start_at = float(sys.argv[7]) if len(sys.argv) > 7 else 0.0
result = {"pid": os.getpid(), "acquired": False, "error": None, "start": None, "end": None}
if start_at:
    time.sleep(max(0.0, start_at - time.time()))  # 同步起跑线,消除进程启动抖动
lock = gate.FileLock(
    Path(lock_path),
    timeout_s=float(timeout_s),
    stale_s=float(stale_s),
    heartbeat_s=float(heartbeat_s),
)
try:
    lock.acquire()
    result["acquired"] = True
    result["start"] = time.time()
    time.sleep(float(hold_s))
    result["end"] = time.time()
    result["ownership_lost"] = lock.ownership_lost
    lock.release()
except TimeoutError as exc:
    result["error"] = f"{type(exc).__name__}: {exc}"
Path(out).write_text(json.dumps(result), encoding="utf-8")
'''

_CRASH_RUNNER = r'''
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\ttxt")
from ttxt.model_gate import gate  # noqa: E402

lock = gate.FileLock(sys.argv[1], timeout_s=2.0, stale_s=float(sys.argv[2]), heartbeat_s=0.2)
lock.acquire()
Path(sys.argv[3]).write_text(str(os.getpid()), encoding="ascii")
time.sleep(0.3)
os._exit(9)  # 模拟进程崩溃:不 release,由 OS 关闭句柄
'''


# 子进程隔离引导:pytest 的 monkeypatch 不会跨进程传播,子进程必须自己把 cfg.LOG_DIR
# 指向 conftest 导出的 TTXT_TEST_LOG_DIR,否则 gate.* 事件会写进生产 09_logs(实测 7 行/次)。
_CHILD_ISOLATION_BOOTSTRAP = r'''
import os
import sys
from pathlib import Path

sys.path.insert(0, r"D:\ttxt")
_child_log_dir = os.environ.get("TTXT_TEST_LOG_DIR")
if _child_log_dir:
    from ttxt import config as _child_cfg  # noqa: E402

    _child_cfg.LOG_DIR = Path(_child_log_dir)
else:  # 缺少隔离变量时也不许写生产日志:退到 07_tmp 下的兜底目录
    from ttxt import config as _child_cfg  # noqa: E402

    _child_cfg.LOG_DIR = Path(r"D:\ttxt\07_tmp\child_logs_fallback")
'''


def _spawn_runner(tmp_path: Path, name: str, source: str) -> Path:
    script = tmp_path / name
    script.write_text(_CHILD_ISOLATION_BOOTSTRAP + source, encoding="utf-8")
    return script


def _run(script: Path, args: list[str], tmp_path: Path) -> subprocess.CompletedProcess:
    log_path = tmp_path / (script.name + ".stderr.log")
    with log_path.open("wb") as stderr:
        return subprocess.run(
            [sys.executable, "-X", "utf8", str(script), *args],
            cwd=str(tmp_path),
            stdout=subprocess.DEVNULL,
            stderr=stderr,
            timeout=90,
        )


# ---------------------------------------------------------------- ① / ② 判定阈值与身份


def test_stale_longer_than_timeout_never_reclaims_live_holder(tmp_path):
    """配置错误(stale > timeout)时:等待方超时退出,但活锁完好无损。"""
    lock_path = tmp_path / "gate.lock"
    holder = gate.FileLock(lock_path, timeout_s=0.4, stale_s=3600.0, heartbeat_s=0.2)
    holder.acquire()
    stale_ts = time.time() - 99999
    os.utime(lock_path, (stale_ts, stale_ts))  # 人为把 mtime 打旧,模拟时钟/落盘异常
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        gate.FileLock(lock_path, timeout_s=0.4, stale_s=3600.0).acquire()
    assert time.monotonic() - started < 2.0
    assert lock_path.exists()
    pid, _mtime, token, _created = gate._read_lock(lock_path)
    assert pid == os.getpid()
    assert token == holder._token
    assert not holder.ownership_lost
    holder.release()
    assert not lock_path.exists()


def test_live_holder_with_matching_create_time_is_not_reclaimed(tmp_path):
    """持有者 PID 就是本进程且启动时刻一致:stale_s=0 也不得回收。"""
    lock_path = tmp_path / "gate.lock"
    holder = gate.FileLock(lock_path, timeout_s=0.4, stale_s=10.0, heartbeat_s=0.2)
    holder.acquire()
    stale_ts = time.time() - 99999
    os.utime(lock_path, (stale_ts, stale_ts))
    with pytest.raises(TimeoutError):
        gate.FileLock(lock_path, timeout_s=0.4, stale_s=0.0).acquire()
    assert lock_path.exists()
    holder.release()


def test_pid_reuse_is_detected_and_reclaimed(tmp_path):
    """锁里记录的 PID 仍存活但启动时刻不符 → 判定旧持有者已消失,回收锁。"""
    lock_path = tmp_path / "gate.lock"
    payload = (
        f"{os.getpid():<10} {int(time.time()):<12} "
        f"{'deadbeefdeadbeefdeadbeefdeadbeef':<32} {1000000.000:<16}"
    )
    lock_path.write_text(payload, encoding="ascii")
    stale_ts = time.time() - 500
    os.utime(lock_path, (stale_ts, stale_ts))
    alive, reason = gate._holder_alive(os.getpid(), 1000000.0)
    assert not alive and reason == "pid_reused"
    lock = gate.FileLock(lock_path, timeout_s=2.0, stale_s=1.0, heartbeat_s=0.2)
    lock.acquire()
    pid, _mtime, token, created = gate._read_lock(lock_path)
    assert pid == os.getpid()
    assert token == lock._token
    assert created is not None and created > 1000000.0
    lock.release()
    assert not lock_path.exists()


def test_dead_process_lock_is_reclaimed_and_gone_pid_is_not_alive():
    """崩溃/退出进程留下的锁到期后回收;不存在的 PID 判死。"""
    assert gate._pid_alive(99999999) is False
    alive, reason = gate._holder_alive(99999999, None)
    assert not alive and reason == "process_gone_or_exited"


# ---------------------------------------------------------------- ③ 续租


def test_holder_renews_and_survives_idle_beyond_stale(tmp_path):
    """持锁但无业务活动:mtime 被心跳刷新,超过 stale_s 也不会被别的进程抢走。"""
    lock_path = tmp_path / "gate.lock"
    holder = gate.FileLock(lock_path, timeout_s=1.0, stale_s=0.6, heartbeat_s=0.2)
    holder.acquire()
    try:
        before = lock_path.stat().st_mtime_ns
        time.sleep(1.0)  # 空闲时间 > stale_s
        after = lock_path.stat().st_mtime_ns
        assert after > before, "持锁期间没有续租,mtime 未刷新"
        _pid, mtime, token, _created = gate._read_lock(lock_path)
        assert token == holder._token
        assert time.time() - mtime < 0.6
        with pytest.raises(TimeoutError):
            gate.FileLock(lock_path, timeout_s=0.4, stale_s=0.6).acquire()
        assert lock_path.exists()
        assert not holder.ownership_lost
    finally:
        holder.release()
    assert not lock_path.exists()


def test_release_detects_foreign_lock_file_and_keeps_it(tmp_path):
    """锁文件被他人接管时:release 只告警,不删除别人的锁。"""
    lock_path = tmp_path / "gate.lock"
    lock_path.write_text("54321 0", encoding="ascii")
    gate.FileLock(lock_path).release()
    assert lock_path.exists()


# ---------------------------------------------------------------- ④ 跨进程


def test_two_processes_serialized_by_lock(tmp_path):
    """两个独立 Python 进程同时起跑竞争:各自持锁区间不重叠,后到者排队而非并发。"""
    runner = _spawn_runner(tmp_path, "lock_runner.py", _RUNNER)
    lock_path = tmp_path / "gate.lock"
    results = [tmp_path / f"result_{index}.json" for index in range(2)]
    start_at = time.time() + 3.0  # 同步起跑线,消除进程启动抖动
    procs = [
        _run_start(runner, [str(results[index]), str(lock_path), "1.0", "10", "3.0", "0.3", str(start_at)], tmp_path)
        for index in range(2)
    ]
    payloads = []
    for process, result_path in zip(procs, results):
        process.wait(timeout=90)
        payloads.append(json.loads(result_path.read_text(encoding="utf-8")))
    assert all(item["acquired"] for item in payloads), payloads
    assert not any(item["ownership_lost"] for item in payloads), payloads
    intervals = sorted((item["start"], item["end"]) for item in payloads)
    (_start_prev, end_prev), (start_next, _end_next) = intervals
    assert end_prev <= start_next + 1e-6, f"两进程持锁区间重叠:{intervals}"


def _run_start(script: Path, args: list[str], tmp_path: Path) -> subprocess.Popen:
    log_path = tmp_path / (script.name + ".stderr.log")
    with log_path.open("wb") as stderr:
        return subprocess.Popen(
            [sys.executable, "-X", "utf8", str(script), *args],
            cwd=str(tmp_path),
            stdout=subprocess.DEVNULL,
            stderr=stderr,
        )


def test_only_one_of_racing_processes_gets_lock(tmp_path):
    """同一时刻抢占:timeout_s 极短,只有一个进程拿到锁,其余全部超时退出。"""
    runner = _spawn_runner(tmp_path, "lock_racer.py", _RUNNER)
    lock_path = tmp_path / "gate.lock"
    racers = 4
    results = [tmp_path / f"race_{index}.json" for index in range(racers)]
    start_at = time.time() + 3.0
    procs = [
        _run_start(runner, [str(results[index]), str(lock_path), "3.0", "0.4", "5.0", "0.3", str(start_at)], tmp_path)
        for index in range(racers)
    ]
    payloads = []
    for process, result_path in zip(procs, results):
        process.wait(timeout=90)
        payloads.append(json.loads(result_path.read_text(encoding="utf-8")))
    winners = [item for item in payloads if item["acquired"]]
    losers = [item for item in payloads if not item["acquired"]]
    assert len(winners) == 1, f"预期只有一个获胜者:{payloads}"
    assert len(losers) == racers - 1
    assert all(item["error"] and item["error"].startswith("TimeoutError") for item in losers), payloads
    assert not lock_path.exists(), "获胜者释放后锁文件应消失"


def test_crashed_process_lock_is_recovered_by_next_process(tmp_path):
    """持锁进程崩溃(os._exit,不释放):下一进程等过 stale_s 后接管。"""
    crash = _spawn_runner(tmp_path, "lock_crash.py", _CRASH_RUNNER)
    pid_file = tmp_path / "holder.pid"
    lock_path = tmp_path / "gate.lock"
    completed = _run(crash, [str(lock_path), "1.0", str(pid_file)], tmp_path)
    assert completed.returncode == 9
    dead_pid = int(pid_file.read_text(encoding="ascii"))
    assert gate._pid_alive(dead_pid) is False, f"模拟崩溃的进程 {dead_pid} 仍在运行"
    with pytest.raises(TimeoutError):
        gate.FileLock(lock_path, timeout_s=0.4, stale_s=5.0).acquire()
    assert lock_path.exists()
    recovered = gate.FileLock(lock_path, timeout_s=5.0, stale_s=1.0, heartbeat_s=0.2)
    recovered.acquire()
    pid, _mtime, token, _created = gate._read_lock(lock_path)
    assert pid == os.getpid() and token == recovered._token
    recovered.release()
    assert not lock_path.exists()


# ---------------------------------------------------------------- ⑤ / ⑥ / ⑦ 隔离


def test_default_gate_lock_is_redirected_to_tmp(tmp_path):
    """默认锁路径已由 conftest 重定向到临时目录;生产锁路径全程不存在。"""
    production_lock = cfg.CONTROL_DIR / "model_gate" / "gate.lock"
    assert Path(gate.GATE_LOCK) == tmp_path / "gate.lock"
    assert not production_lock.exists()
    lock = gate.FileLock()  # 不显式传路径
    lock.acquire()
    assert (tmp_path / "gate.lock").exists()
    assert not production_lock.exists()
    lock.release()
    assert not (tmp_path / "gate.lock").exists()
    assert not production_lock.exists()


def test_queue_and_gate_only_touch_temp_system_db(tmp_path):
    """队列与闸门只用临时 system.db;生产 system.db 只读且字节不变。"""
    db_path = system_db.init_system_db(tmp_path / "system.db")
    production_db = cfg.CONTROL_DIR / "system.db"
    assert db_path != production_db
    assert tmp_path in db_path.parents

    def digest(path: Path) -> str:
        current = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                current.update(block)
        return current.hexdigest()

    before = digest(production_db)
    prod_sidecars_before = sidecars(production_db)
    lock_path = tmp_path / "gate.lock"
    job_id = queue.enqueue("chat", {"prompt": "hi"}, category="demo", db_path=db_path)
    with gate.FileLock(lock_path, timeout_s=1.0, stale_s=1.0):
        row = queue.claim_next(db_path=db_path)
        assert row is not None and row["id"] == job_id
        queue.finish(job_id, {"text": "ok"}, db_path=db_path)
    # 临时库用普通连接核对。实测(07_tmp/wal_probe2.py):对 WAL 库使用 ``mode=ro`` URI
    # 会新建 -wal/-shm 并且关闭后不清理(只读连接无权 checkpoint),这正是之前污染
    # 生产目录的根因;因此“只读 URI”只能用于 tmp 副本,不能用于需要保持零残留的目录。
    con = sqlite3.connect(db_path)
    try:
        assert con.execute("SELECT status FROM model_jobs WHERE id=?", (job_id,)).fetchone()[0] == "done"
        assert con.execute("SELECT COUNT(*) FROM model_jobs").fetchone()[0] == 1
    finally:
        con.close()
    assert digest(production_db) == before, "测试改动了生产 system.db"
    # queue 每个调用都关闭连接:强制回收后临时目录不应残留 WAL 边车。
    gc.collect()
    time.sleep(0.1)
    residue = sorted(p.name for p in tmp_path.glob("system.db*") if p.name.endswith(("-wal", "-shm", "-journal")))
    assert not residue, f"queue 调用没有关闭连接,临时目录残留:{residue}"
    # 本测试只连接临时库:生产目录的 WAL 边车集合不得因本测试而变化。
    assert sidecars(production_db) == prod_sidecars_before, "测试连接了生产 system.db"


def test_production_control_state_untouched_midway(production_state_untouched):
    """会话快照夹具已记录生产指纹;此处核对队列目录与库文件仍未变化。"""
    assert production_state_untouched["00_control/model_gate"] == []
    for key in ("00_control/system.db", "01_categories/technology/articles.db", "00_control/worker.lock"):
        path = cfg.PROJECT_ROOT / key
        entry = production_state_untouched[key]
        assert path.exists(), f"生产文件消失:{path}"
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        stat = path.stat()
        live = {"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": digest.hexdigest()}
        assert live == entry, f"生产状态已变化:{key}\n快照={entry}\n当前={live}"
    assert not (cfg.CONTROL_DIR / "model_gate" / "gate.lock").exists()
