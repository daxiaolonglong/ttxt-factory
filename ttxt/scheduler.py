"""B4 本地调度器:保守执行 —— 只入队采集任务与跑本地分析,绝不触发写作/发布/模型。

- 任务定义在 config.scheduler.jobs(id/kind/interval_min/enabled);
- 状态记录在 00_control/scheduler_state.json(last_run_at),按间隔判断到期;
- crawl_enqueue 只创建 queued 任务(同来源去重),由 `ttxt crawl-queue --run` 或人工消费;
- kind 命中 write/publish/model 直接拒绝(设计上不可能被调度执行);
- 支持 --once(跑一轮到期任务)、--status(查看到期计划)、--daemon(常驻轮询)。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ttxt import config as cfg

FORBIDDEN_KINDS = {"write", "publish", "model", "model_drain", "draft"}


def default_state_path() -> Path:
    return cfg.CONTROL_DIR / "scheduler_state.json"


def load_jobs(conf: dict | None = None) -> dict:
    conf = conf if conf is not None else cfg.load_config()
    scheduler = conf.get("scheduler", {}) or {}
    jobs = [job for job in scheduler.get("jobs", []) if isinstance(job, dict) and job.get("id")]
    for job in jobs:
        if job.get("kind") in FORBIDDEN_KINDS:
            raise ValueError(f"调度器禁止配置 {job['kind']!r} 类任务(写作/发布/模型不允许被自动调度)")
    return {"enabled": bool(scheduler.get("enabled", True)), "jobs": jobs}


def read_state(state_path: Path | None = None) -> dict:
    path = Path(state_path) if state_path else default_state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def write_state(state: dict, state_path: Path | None = None) -> None:
    path = Path(state_path) if state_path else default_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def is_due(job: dict, state: dict, now: datetime) -> bool:
    entry = state.get(job["id"]) or {}
    last = _parse_iso(entry.get("last_run_at", ""))
    if last is None:
        return True
    interval = timedelta(minutes=float(job.get("interval_min", 60)))
    return now - last >= interval


def due_jobs(jobs: list[dict], state: dict, now: datetime) -> list[dict]:
    return [job for job in jobs if job.get("enabled", True) and is_due(job, state, now)]


def _job_crawl_enqueue(job: dict) -> dict:
    from ttxt.crawler import runner, sources as source_loader, store

    system_db = Path(job["system_db"]) if job.get("system_db") else None
    sources_path = Path(job["sources_path"]) if job.get("sources_path") else None
    enqueued = deduped = rejected = 0
    errors: list[str] = []
    for source in source_loader.load_sources(path=sources_path):
        if not source.enabled:
            continue
        try:
            category = runner.resolve_category(source, None)
        except ValueError as exc:
            errors.append(f"{source.id}: {exc}")
            rejected += 1
            continue
        if store.find_pending_crawl_job(source.id, category, db_path=system_db) is not None:
            deduped += 1
            continue
        store.create_crawl_job(
            source.id,
            category,
            db_path=system_db,
            meta={"reason": "scheduler", "job": job["id"], "dispatched_via": "scheduler"},
        )
        enqueued += 1
    return {"enqueued": enqueued, "deduped": deduped, "routing_rejected": rejected, "errors": errors}


def _job_analyze(job: dict) -> dict:
    from ttxt.analysis import analyze as analyzer

    if job.get("category"):
        categories = [str(job["category"])]
    else:
        categories = list(cfg.load_config().get("categories", []))
        if (cfg.CATEGORIES_DIR / "demo").exists():
            categories.append("demo")
    results: dict[str, Any] = {}
    for category in categories:
        try:
            analyzer.run_analysis(category)
            results[category] = "ok"
        except Exception as exc:  # noqa: BLE001 - 类目级隔离
            results[category] = f"error: {type(exc).__name__}: {exc}"
    return results


def _job_recover(job: dict) -> dict:
    from ttxt.crawler import store as crawler_store
    from ttxt.model_gate import queue as model_queue

    system_db = Path(job["system_db"]) if job.get("system_db") else None
    return {
        "model_stale_requeued": model_queue.requeue_stale_running(db_path=system_db),
        "crawl_stale_marked_failed": crawler_store.recover_stale_crawl_jobs(db_path=system_db),
        "crawl_failed_requeued": crawler_store.requeue_failed_crawl_jobs(db_path=system_db),
    }


def _job_housekeep(job: dict) -> dict:
    from ttxt import housekeep

    return housekeep.run(clean=bool(job.get("clean", False)))


_JOB_RUNNERS = {
    "crawl_enqueue": _job_crawl_enqueue,
    "analyze": _job_analyze,
    "recover": _job_recover,
    "housekeep": _job_housekeep,
}


def run_job(job: dict) -> dict:
    kind = job.get("kind")
    if kind in FORBIDDEN_KINDS:
        raise ValueError(f"调度器拒绝执行 {kind!r} 任务(只允许入队采集与本地分析)")
    runner_fn = _JOB_RUNNERS.get(str(kind))
    if runner_fn is None:
        raise ValueError(f"未知调度任务 kind: {kind!r}(可用: {sorted(_JOB_RUNNERS)})")
    return runner_fn(job)


def run_due(
    now: datetime | None = None,
    state_path: Path | None = None,
    conf: dict | None = None,
) -> dict:
    now = now or datetime.now()
    config_block = load_jobs(conf)
    if not config_block["enabled"]:
        return {"skipped": "scheduler.disabled", "ran": []}
    state = read_state(state_path)
    due = due_jobs(config_block["jobs"], state, now)
    ran: list[dict] = []
    for job in due:
        entry: dict[str, Any] = {"id": job["id"], "kind": job.get("kind")}
        try:
            entry["result"] = run_job(job)
            entry["status"] = "ok"
        except Exception as exc:  # noqa: BLE001 - 单任务失败不影响其它任务
            entry["status"] = "error"
            entry["error"] = f"{type(exc).__name__}: {exc}"
        entry["finished_at"] = cfg.now_iso()
        state[job["id"]] = {
            "last_run_at": entry["finished_at"],
            "last_status": entry["status"],
            "last_result": entry.get("result"),
            "last_error": entry.get("error"),
        }
        ran.append(entry)
    write_state(state, state_path)
    return {"ran": ran, "skipped_not_due": len(config_block["jobs"]) - len(due)}


def status_report(state_path: Path | None = None, conf: dict | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    config_block = load_jobs(conf)
    state = read_state(state_path)
    rows = []
    for job in config_block["jobs"]:
        entry = state.get(job["id"]) or {}
        last = _parse_iso(entry.get("last_run_at", ""))
        interval = timedelta(minutes=float(job.get("interval_min", 60)))
        rows.append(
            {
                "id": job["id"],
                "kind": job.get("kind"),
                "enabled": job.get("enabled", True),
                "interval_min": job.get("interval_min", 60),
                "last_run_at": entry.get("last_run_at"),
                "last_status": entry.get("last_status"),
                "due": bool(job.get("enabled", True)) and (last is None or now - last >= interval),
            }
        )
    return {"enabled": config_block["enabled"], "jobs": rows}


def daemon(interval_s: int = 300, state_path: Path | None = None) -> None:
    """常驻轮询:每 interval_s 检查一次到期任务。Ctrl+C 退出。"""
    while True:
        report = run_due(state_path=state_path)
        if report.get("ran"):
            print(json.dumps(report, ensure_ascii=False))
        time.sleep(max(30, int(interval_s)))
