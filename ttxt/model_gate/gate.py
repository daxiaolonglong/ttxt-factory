"""串行闸门:文件锁 + FIFO 队列,任何时刻只处理一个模型任务。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ttxt import config as cfg
from ttxt.logging import get_logger

from . import client, queue

LOG = get_logger("model_gate")
GATE_LOCK = cfg.CONTROL_DIR / "model_gate" / "gate.lock"


class FileLock:
    def __init__(self, path: Path = GATE_LOCK, timeout_s: float = 600.0, stale_s: float = 1800.0) -> None:
        self.path = Path(path)
        self.timeout_s = timeout_s
        self.stale_s = stale_s
        self._fd: int | None = None

    def acquire(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, f"{os.getpid()} {time.time():.0f}".encode("ascii"))
                return self
            except FileExistsError:
                self._clear_stale()
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"model gate lock busy: {self.path}")
                time.sleep(0.2)

    def _clear_stale(self) -> None:
        try:
            age = time.time() - self.path.stat().st_mtime
        except FileNotFoundError:
            return
        if age > self.stale_s:
            try:
                self.path.unlink()
                LOG.warn("gate.lock_stale_removed", path=str(self.path), age_s=round(age))
            except FileNotFoundError:
                pass

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

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
    with FileLock(gate_lock or GATE_LOCK, timeout_s=wait_timeout_s):
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
    with FileLock(gate_lock or GATE_LOCK, timeout_s=wait_timeout_s):
        for _ in range(max_jobs):
            row = queue.claim_next(db_path=db_path)
            if row is None:
                break
            results.append(_run_one(row, processor, db_path))
    return {"processed": results}
