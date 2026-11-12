"""模型任务持久队列(system.db.model_jobs),崩溃可恢复、FIFO。"""

from __future__ import annotations

import json
from pathlib import Path

from ttxt import config as cfg
from ttxt.db import connect, system_db_path


def _db(db_path: Path | str | None) -> Path:
    return Path(db_path) if db_path is not None else system_db_path()


def enqueue(job_type: str, payload: dict, category: str | None = None, db_path: Path | None = None) -> int:
    con = connect(_db(db_path))
    try:
        cursor = con.execute(
            "INSERT INTO model_jobs(category, job_type, payload_json, status, created_at) VALUES (?, ?, ?, 'queued', ?)",
            (category, job_type, json.dumps(payload, ensure_ascii=False), cfg.now_iso_ms()),
        )
        con.commit()
        return int(cursor.lastrowid)
    finally:
        con.close()


def claim_next(db_path: Path | None = None) -> dict | None:
    """原子领取最早排队的任务;无任务返回 None。"""
    con = connect(_db(db_path))
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM model_jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if row is None:
            con.rollback()
            return None
        updated = con.execute(
            "UPDATE model_jobs SET status='running', started_at=?, attempts=attempts+1 WHERE id=? AND status='queued'",
            (cfg.now_iso_ms(), row["id"]),
        )
        if updated.rowcount != 1:
            con.rollback()
            return None
        con.commit()
        claimed = dict(row)
        claimed["status"] = "running"
        claimed["attempts"] = int(row["attempts"]) + 1
        return claimed
    finally:
        con.close()


def finish(job_id: int, result: dict, db_path: Path | None = None) -> None:
    con = connect(_db(db_path))
    try:
        con.execute(
            "UPDATE model_jobs SET status='done', finished_at=?, result_json=? WHERE id=?",
            (cfg.now_iso_ms(), json.dumps(result, ensure_ascii=False), job_id),
        )
        con.commit()
    finally:
        con.close()


def record_failure(job_id: int, error: str, max_attempts: int = 3, db_path: Path | None = None) -> str:
    con = connect(_db(db_path))
    try:
        row = con.execute("SELECT attempts FROM model_jobs WHERE id=?", (job_id,)).fetchone()
        attempts = int(row["attempts"]) if row else 0
        status = "queued" if attempts < max_attempts else "failed"
        con.execute(
            "UPDATE model_jobs SET status=?, last_error=?, finished_at=? WHERE id=?",
            (status, error[:2000], cfg.now_iso_ms() if status == "failed" else None, job_id),
        )
        con.commit()
        return status
    finally:
        con.close()


def get(job_id: int, db_path: Path | None = None) -> dict | None:
    con = connect(_db(db_path))
    try:
        row = con.execute("SELECT * FROM model_jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def stats(db_path: Path | None = None) -> dict[str, int]:
    con = connect(_db(db_path))
    try:
        rows = con.execute("SELECT status, COUNT(*) AS n FROM model_jobs GROUP BY status").fetchall()
        return {row["status"]: int(row["n"]) for row in rows}
    finally:
        con.close()
