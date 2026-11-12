"""P5 测试:队列生命周期、失败重试、跨进程文件锁、线程串行、真实模型调用。"""

import sqlite3
import threading
import time

import pytest

from ttxt import db as system_db
from ttxt.model_gate import client, gate, queue


def _tmp_db(tmp_path):
    return system_db.init_system_db(tmp_path / "system.db")


def test_queue_lifecycle(tmp_path):
    db_path = _tmp_db(tmp_path)
    job_id = queue.enqueue("chat", {"prompt": "hi"}, category="demo", db_path=db_path)
    row = queue.claim_next(db_path=db_path)
    assert row is not None
    assert row["id"] == job_id
    assert row["status"] == "running"
    assert queue.claim_next(db_path=db_path) is None

    queue.finish(job_id, {"text": "ok"}, db_path=db_path)
    con = sqlite3.connect(db_path)
    try:
        status, result = con.execute(
            "SELECT status, result_json FROM model_jobs WHERE id=?", (job_id,)
        ).fetchone()
        assert status == "done"
        assert '"text"' in result
    finally:
        con.close()


def test_failure_requeue_then_fail(tmp_path):
    db_path = _tmp_db(tmp_path)
    job_id = queue.enqueue("chat", {"prompt": "x"}, db_path=db_path)
    statuses = []
    for _ in range(3):
        queue.claim_next(db_path=db_path)
        statuses.append(queue.record_failure(job_id, "boom", db_path=db_path))
    assert statuses == ["queued", "queued", "failed"]
    record = queue.get(job_id, db_path=db_path)
    assert record["status"] == "failed"
    assert record["last_error"] == "boom"


def test_file_lock_serializes(tmp_path):
    lock_path = tmp_path / "gate.lock"
    lock = gate.FileLock(lock_path, timeout_s=1.0)
    lock.acquire()
    outcomes = []

    def contender():
        try:
            with gate.FileLock(lock_path, timeout_s=0.3):
                outcomes.append("acquired")
        except TimeoutError:
            outcomes.append("timeout")

    thread = threading.Thread(target=contender)
    thread.start()
    thread.join()
    lock.release()
    assert outcomes == ["timeout"]
    with gate.FileLock(lock_path, timeout_s=1.0):
        pass


def test_serial_processor_threads(tmp_path):
    db_path = _tmp_db(tmp_path)
    lock_path = tmp_path / "gate.lock"
    events = []

    def processor(row):
        started = time.monotonic()
        time.sleep(0.2)
        events.append((started, time.monotonic(), row["id"]))
        return {"text": "ok"}

    def worker(prompt):
        gate.submit_and_run(
            "chat",
            {"prompt": prompt},
            processor=processor,
            db_path=db_path,
            gate_lock=lock_path,
            wait_timeout_s=20,
        )

    threads = [threading.Thread(target=worker, args=(f"job{i}",)) for i in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(events) == 3
    intervals = sorted((start, end) for start, end, _ in events)
    for (_start_prev, end_prev), (start_next, _end_next) in zip(intervals, intervals[1:]):
        assert end_prev <= start_next + 1e-6


def test_wait_ready_timeout():
    with pytest.raises(client.ModelError):
        client.wait_ready(base_url="http://127.0.0.1:9", timeout_s=1.0, poll_s=0.2)


def test_real_model_health_and_chat():
    health = client.health()
    if not health or not health.get("loaded"):
        pytest.skip("Strata 模型未就绪")
    model_ids = client.models()
    assert model_ids
    result = client.chat(
        [{"role": "user", "content": "只回复两个字母:OK"}],
        max_tokens=16,
        temperature=0.0,
        enable_thinking=False,
    )
    assert result["text"].strip() != ""
    assert result["model"]
