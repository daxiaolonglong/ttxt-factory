"""B4 本地调度器:保守执行 —— 只入队采集任务与跑本地分析,绝不触发写作/发布/模型。

- 任务定义在 config.scheduler.jobs(id/kind/interval_min/enabled);
- 状态记录在 00_control/scheduler_state.json(last_run_at),按间隔判断到期;
- crawl_enqueue 只创建 queued 任务(同来源去重),由 `ttxt crawl-queue --run` 或人工消费;
- metrics_fetch(P35)采集公开文章页互动数 → 重标定爆款标签 → 关键词评分,把互动数据自动回流进闭环。
  它**直接用浏览器**,而 Worker 是另一个进程、共用同一份 profile,所以开跑前先确认 crawl 队列空闲
  (有 queued/running 就先等;等不到就整轮跳过,一次请求都不发);
- kind 命中 write/publish/model 直接拒绝(设计上不可能被调度执行);
- 支持 --once(跑一轮到期任务)、--status(查看到期计划)、--run-job ID(手动跑一个任务并记账)、--daemon(常驻轮询)。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ttxt import config as cfg
from ttxt.logging import get_logger

LOG = get_logger("scheduler")

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
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is not None:
        # 状态时间戳(cfg.now_iso)带本地时区偏移,而 run_due/status_report 用
        # datetime.now() 的 naive 本地时间比较;统一转成本地 naive,
        # 否则第二次运行起 aware-naive 混算必抛 TypeError。
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


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


# --- P35:公开互动指标自动回流 ---------------------------------------------------
# 闭环里断掉的这一段:公开页互动数只有手动命令能采(00_control/config.json 里
# metrics.public_pages 有来源、白名单域名、每轮上限和间隔,却没有调度任务调用它)。
# 这里把它接进调度器,并锁死三条口径:
# 1. 默认 dry-run,配置里显式 apply=true 才写生产库;
# 2. 采集前必须让开 Worker:调度进程和 Worker 是两个进程、共用同一份浏览器 profile,
#    同时开浏览器会互抢。队列里有 queued/running 的 crawl 任务就先有界等待,等不到整轮跳过;
# 3. 一个类目失败不能带走其它类目,而且失败/跳过的真实状态要留在状态文件里,不能记成 ok。


def _nap(seconds: float) -> None:
    """等待的空隙单独包一层,测试可以替换成不真等(也不改变判断口径)。"""
    time.sleep(seconds)


def crawl_queue_busy_counts(system_db: Path | None = None) -> dict:
    """crawl 队列里"有人正在用浏览器"的任务数。

    只数 job_type='crawl':Worker 永不认领别的类型,若把非 crawl 任务也算忙,
    指标采集会被永远不会消费的任务饿死。
    """
    from ttxt import db as ttxt_db

    target = Path(system_db) if system_db else ttxt_db.system_db_path()
    con = ttxt_db.connect(target)
    try:
        rows = con.execute(
            "SELECT status, COUNT(*) AS n FROM crawl_jobs "
            "WHERE job_type='crawl' AND status IN ('queued','running') GROUP BY status"
        ).fetchall()
    finally:
        con.close()
    counts = {str(row["status"]): int(row["n"]) for row in rows}
    queued = counts.get("queued", 0)
    running = counts.get("running", 0)
    return {"queued": queued, "running": running, "busy": bool(queued or running)}


def _wait_for_idle_queue(system_db: Path | None, wait_s: float, poll_s: float) -> dict:
    """有界等待队列排空(不是无限等,也不是看一眼就走)。"""
    budget = max(0.0, float(wait_s))
    deadline = time.monotonic() + budget
    counts = crawl_queue_busy_counts(system_db)
    waited = 0.0
    while counts["busy"] and time.monotonic() < deadline:
        step = min(max(float(poll_s), 0.5), deadline - time.monotonic())
        _nap(step)
        waited += step
        counts = crawl_queue_busy_counts(system_db)
    counts["waited_s"] = round(waited, 1)
    counts["wait_budget_s"] = budget
    return counts


def _write_metrics_evidence(evidence_dir: Path, category: str, stamp: str, payload: dict) -> Path:
    """每轮每类目落一份证据文件(失败也落),对账时能对上"这一轮到底发生了什么"。"""
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / f"{category}-{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def _job_metrics_fetch(job: dict) -> dict:
    from ttxt.analysis import analyze as analyzer
    from ttxt.analysis import metrics_import
    from ttxt.crawler import fetcher
    from ttxt.crawler import metrics_pages

    conf = cfg.load_config()
    page_cfg = dict((conf.get("metrics", {}) or {}).get("public_pages", {}) or {})
    limit = int(job.get("limit", page_cfg.get("limit_per_run", 20)))
    sleep_s = float(job.get("sleep_s", page_cfg.get("sleep_s", 2.0)))
    apply = bool(job.get("apply", False))          # 没显式打开就一律 dry-run,不碰生产库
    retry_gone = bool(job.get("retry_gone", False))
    # P36:调度进程与采集 Worker 是两个进程、共用同一份浏览器 profile。这里等锁的时间
    # 必须比 Worker 那侧短:抢不到就明确跳过本轮,而不是把 Worker 正在用的浏览器抢过来。
    lock_wait_s = job.get("lock_wait_s")
    system_db = Path(job["system_db"]) if job.get("system_db") else None
    evidence_dir = Path(job["evidence_dir"]) if job.get("evidence_dir") else cfg.INTELLIGENCE_DIR / "metrics_public"
    categories = [str(job["category"])] if job.get("category") else [str(c) for c in conf.get("categories", [])]

    gate = _wait_for_idle_queue(
        system_db, float(job.get("wait_for_queue_s", 240.0)), float(job.get("queue_poll_s", 10.0))
    )
    if gate["busy"]:
        LOG.warn("scheduler.metrics.skipped_queue_busy", queued=gate["queued"],
                 running=gate["running"], waited_s=gate["waited_s"])
        return {
            "scheduler_status": "skipped_queue_busy",
            "reason": (f"采集队列仍忙(queued={gate['queued']}, running={gate['running']}),"
                       f"已等待 {gate['waited_s']}s;这一轮一个请求都不发"),
            "queue": gate,
            "categories": {},
            "errors": [],
        }

    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    per: dict[str, dict] = {}
    errors: list[str] = []
    # 浏览器用完再同步口径:重标定之后的关键词评分会触发"关键词反哺采集"入队,Worker 是另一个进程、
    # 共用同一份浏览器 profile。先让本轮所有页面采集收尾,再入队,才不会两边同时开浏览器。
    pending_sync: list[str] = []
    browser_busy: list[str] = []

    for category in categories:
        if browser_busy:
            # 已经确认浏览器被别的进程占着,后面的类目没必要再各等一遍 lock_wait_s。
            per[category] = {
                "status": "skipped_browser_busy",
                "reason": f"本轮已确认浏览器 profile 被占用({browser_busy[0]}),该类目不排队",
            }
            continue
        try:
            # 先只读算一遍"这轮打算采谁":没活可干就不开浏览器(起一次浏览器十几秒,还白动 profile)
            plan = metrics_pages.round_plan(category, limit=limit)
            if not plan["candidates"]:
                per[category] = {
                    "status": "no_candidates",
                    "host_skipped": plan["host_skipped"],
                    "known_gone_skipped": plan["known_gone_skipped"],
                }
                continue
            report = metrics_pages.collect_with_browser(
                category, limit=limit, apply=apply, sleep_s=sleep_s, retry_gone=retry_gone,
                lock_wait_s=lock_wait_s,
            )
            _write_metrics_evidence(evidence_dir, category, stamp,
                                    {"category": category, "job": job.get("id"), "run_at": cfg.now_iso(),
                                     "plan": plan, **report})
            if not apply:
                per[category] = {
                    "status": "dry_run",
                    "fetched": int(report.get("fetched", 0) or 0),
                    # 采集器在干跑时给 would_write(取到计数、真跑就会写的条数),别拿 written 冒充
                    "would_write": int(report.get("would_write", 0) or 0),
                    "known_gone_skipped": int(report.get("known_gone_skipped", 0) or 0),
                    "outcome_counts": report.get("outcome_counts", {}),
                }
                continue
            per[category] = {
                "status": "written",  # 已写库,口径还没同步;同步完成才改写成 ok
                "fetched": int(report.get("fetched", 0) or 0),
                "written": int(report.get("written", 0) or 0),
                "updated": int(report.get("updated", 0) or 0),
                "snapshots": int(report.get("snapshots", 0) or 0),
                "known_gone_skipped": int(report.get("known_gone_skipped", 0) or 0),
                "link_status_recorded": int(report.get("link_status_recorded", 0) or 0),
                "link_status_cleared": int(report.get("link_status_cleared", 0) or 0),
                "row_errors": len(report.get("row_errors") or []),
                "outcome_counts": report.get("outcome_counts", {}),
            }
            pending_sync.append(category)
        except fetcher.BrowserBusyError as exc:
            # P36:浏览器被采集 Worker 占着 —— 这是"让路",不是故障,不能记成 error。
            # 也不重试、不硬抢:本轮跳过,下一轮(6 小时后)再来。
            holder = exc.holder or {}
            waited = round(float(exc.waited_s), 1)
            browser_busy.append(category)
            LOG.warn(
                "scheduler.metrics.skipped_browser_busy",
                category=category,
                waited_s=waited,
                holder_tag=holder.get("tag") or "未知",
                holder_pid=holder.get("pid") or "?",
                lock=str(exc.lock_path),
            )
            per[category] = {
                "status": "skipped_browser_busy",
                "reason": (
                    f"等浏览器 profile 锁 {waited}s 没等到"
                    f"(持有者 tag={holder.get('tag') or '未知'} pid={holder.get('pid') or '?'});"
                    "该类目不启动浏览器、不发请求,等采集 Worker 用完"
                ),
                "browser_wait_s": waited,
            }
        except Exception as exc:  # noqa: BLE001 - 类目之间互不牵连,但必须留下明确状态
            message = f"{type(exc).__name__}: {exc}"
            errors.append(f"{category}: {message}")
            per[category] = {"status": "error", "error": message}
            _write_metrics_evidence(evidence_dir, category, stamp,
                                    {"category": category, "job": job.get("id"), "run_at": cfg.now_iso(),
                                     "error": message})
            LOG.error("scheduler.metrics.category_failed", category=category, error=message)

    # 写库之后必须同步口径:重标定爆款标签 + 关键词评分,互动数据才算真正回流到选题决策
    for category in pending_sync:
        try:
            labels = metrics_import.relabel_after_import(category)
            analysis = analyzer.run_analysis(category)
            per[category].update({
                "status": "ok",
                "labels": labels if isinstance(labels, dict) else {},
                "analysis": analysis if isinstance(analysis, dict) else {},
            })
            # 重标定用的是**现有阈值**,不是重新算阈值。实测样本涨了但阈值快照还是旧的,
            # 要在回执里说清(重新标定是每轮标准动作第 3 步,不自动做:爆款的定义不能悄悄漂移)。
            viral = analysis.get("viral", {}) if isinstance(analysis, dict) else {}
            tiers = viral.get("tiers", {}) if isinstance(viral, dict) else {}
            calib = viral.get("calibration", {}) if isinstance(viral, dict) else {}
            measured = int(tiers.get("measured", 0) or 0)
            cal_sample = int(calib.get("sample", 0) or 0)
            per[category]["measured"] = measured
            if measured >= 30 and cal_sample < measured:
                per[category]["calibration_hint"] = (
                    f"实测样本已有 {measured} 条,阈值快照还是 {cal_sample} 条时的;"
                    f"要更新爆款口径就执行 ttxt metrics --category {category} --calibrate")
        except Exception as exc:  # noqa: BLE001
            message = f"{type(exc).__name__}: {exc}"
            errors.append(f"{category} 口径同步: {message}")
            per[category].update({"status": "written_sync_failed", "sync_error": message})
            LOG.error("scheduler.metrics.sync_failed", category=category, error=message)

    # 忙导致跳过的类目不算故障;但只要有一个类目真跑成了,整轮就是 partial,不能报 ok
    worked = [c for c, r in per.items() if r.get("status") in {"ok", "written", "written_sync_failed", "dry_run"}]
    if errors and len(errors) == len(categories):
        status = "error"
    elif errors or (browser_busy and worked):
        status = "partial"
    elif browser_busy:
        status = "skipped_browser_busy"
    else:
        status = "ok"
    LOG.info("scheduler.metrics.round", status=status, apply=apply, limit=limit,
             categories=len(categories), failed=len(errors), browser_busy_skipped=len(browser_busy),
             queue_waited_s=gate["waited_s"])
    return {
        "scheduler_status": status,
        "apply": apply,
        "limit": limit,
        "sleep_s": sleep_s,
        "queue": gate,
        "browser_lock_wait_s": lock_wait_s,
        "browser_busy_skipped": browser_busy,
        "categories": per,
        "errors": errors,
    }


_JOB_RUNNERS = {
    "crawl_enqueue": _job_crawl_enqueue,
    "analyze": _job_analyze,
    "recover": _job_recover,
    "housekeep": _job_housekeep,
    "metrics_fetch": _job_metrics_fetch,
}


def run_job(job: dict) -> dict:
    kind = job.get("kind")
    if kind in FORBIDDEN_KINDS:
        raise ValueError(f"调度器拒绝执行 {kind!r} 任务(只允许入队采集与本地分析)")
    runner_fn = _JOB_RUNNERS.get(str(kind))
    if runner_fn is None:
        raise ValueError(f"未知调度任务 kind: {kind!r}(可用: {sorted(_JOB_RUNNERS)})")
    return runner_fn(job)


def _record_run(job: dict, state: dict) -> dict:
    """跑一个任务并把结果记账。任务可以自行声明一个**比 ok 更差**的状态(不能把失败伪装成成功)。"""
    entry: dict[str, Any] = {"id": job["id"], "kind": job.get("kind")}
    try:
        entry["result"] = run_job(job)
        entry["status"] = "ok"
        declared = entry["result"].get("scheduler_status") if isinstance(entry["result"], dict) else None
        if declared and declared != "ok":
            # 例如指标采集:部分类目失败=partial、队列忙=skipped_queue_busy,都不是"成功"
            entry["status"] = str(declared)
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
    return entry


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
    ran: list[dict] = [_record_run(job, state) for job in due]
    write_state(state, state_path)
    return {"ran": ran, "skipped_not_due": len(config_block["jobs"]) - len(due)}


def run_specific(job_id: str, state_path: Path | None = None, conf: dict | None = None) -> dict:
    """手动跑指定任务并照常记账(不等到期)。用来验证新接上的任务,不用改间隔、不用等时钟。"""
    config_block = load_jobs(conf)
    job = next((item for item in config_block["jobs"] if item.get("id") == job_id), None)
    if job is None:
        raise ValueError(f"找不到调度任务 id={job_id!r}(可用: {[item.get('id') for item in config_block['jobs']]})")
    state = read_state(state_path)
    entry = _record_run(job, state)
    write_state(state, state_path)
    return {"ran": [entry], "job_id": job_id}


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
