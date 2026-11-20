"""面板数据接口:全部来自真实 SQLite 与结构化日志,不提供模拟数据。"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ttxt import config as cfg
from ttxt import housekeep
from ttxt.crawler import store
from ttxt.db import connect, system_db_path
from ttxt.model_gate import client as model_client
from ttxt.model_gate import queue as model_queue


def _count(slug: str) -> int:
    try:
        return store.count_articles(slug)
    except Exception:  # noqa: BLE001 - 类目库缺失时按 0 处理
        return 0


def categories() -> list[dict]:
    result: list[dict] = []
    for slug in cfg.load_config().get("categories", []):
        result.append({"slug": slug, "demo": False, "articles": _count(slug)})
    if (cfg.CATEGORIES_DIR / "demo").exists():
        result.append({"slug": "demo", "demo": True, "articles": _count("demo")})
    return result


def overview() -> dict:
    system = connect(system_db_path())
    try:
        crawl = {
            row["status"]: int(row["n"])
            for row in system.execute("SELECT status, COUNT(*) AS n FROM crawl_jobs GROUP BY status")
        }
        model = {
            row["status"]: int(row["n"])
            for row in system.execute("SELECT status, COUNT(*) AS n FROM model_jobs GROUP BY status")
        }
    finally:
        system.close()
    disk = housekeep.report()
    return {
        "categories": categories(),
        "crawl_jobs": crawl,
        "model_jobs": model,
        "strata": model_client.health(),
        "disk": {
            "project_total_gb": disk["project_total_gb"],
            "budget_gb": disk["budget_gb"],
            "free_gb": disk["free_gb"],
            "within_budget": disk["within_budget"],
        },
        "schema_version": store.SCHEMA_VERSION,
    }


def _category_db(slug: str) -> Path | None:
    if not store.SLUG_RE.match(slug):
        raise ValueError(f"invalid category: {slug}")
    path = cfg.CATEGORIES_DIR / slug / "articles.db"
    return path if path.exists() else None


def articles(slug: str, limit: int = 20, offset: int = 0, q: str = "") -> dict:
    path = _category_db(slug)
    if path is None:
        return {"total": 0, "items": []}
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    con = connect(path)
    try:
        where = ""
        params: tuple = ()
        if q:
            where = "WHERE title LIKE ? OR coalesce(cleaned_content, content) LIKE ?"
            params = (f"%{q}%", f"%{q}%")
        total = int(con.execute(f"SELECT COUNT(*) FROM articles {where}", params).fetchone()[0])
        rows = con.execute(
            f"SELECT id, title, source, published_at, text_length, cluster_id FROM articles {where} "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return {"total": total, "items": [dict(row) for row in rows]}
    finally:
        con.close()


def keywords(slug: str) -> list[dict]:
    path = _category_db(slug)
    if path is None:
        return []
    con = connect(path)
    try:
        rows = con.execute(
            """
            SELECT k.id, k.keyword, k.origin, k.status,
                   coalesce(p.collected, 0) AS collected,
                   coalesce(p.quality_articles, 0) AS quality_articles,
                   p.avg_text_length, p.score
            FROM keywords k
            LEFT JOIN keyword_performance p ON p.keyword_id = k.id
            ORDER BY coalesce(p.score, 0) DESC, k.id
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        con.close()


def analysis(slug: str) -> dict:
    path = _category_db(slug)
    if path is None:
        return {
            "articles": 0,
            "avg_text_length": 0,
            "clusters": 0,
            "viral_tiers": {},
            "viral_labels": {},
            "knowledge_assets": [],
            "drafts": [],
            "evaluations": [],
        }
    con = connect(path)
    try:
        tiers = {
            row["confidence_tier"]: int(row["n"])
            for row in con.execute(
                "SELECT confidence_tier, COUNT(*) AS n FROM viral_labels GROUP BY confidence_tier"
            )
        }
        labels = {
            row["label"]: int(row["n"])
            for row in con.execute("SELECT label, COUNT(*) AS n FROM viral_labels GROUP BY label")
        }
        clusters = int(con.execute("SELECT COUNT(*) FROM article_clusters").fetchone()[0])
        total = int(con.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
        avg_len = float(con.execute("SELECT AVG(text_length) FROM articles").fetchone()[0] or 0)
        assets = [
            dict(row)
            for row in con.execute(
                "SELECT kind, statement, support_count, confidence, updated_at FROM knowledge_assets ORDER BY id"
            )
        ]
        drafts = [
            dict(row)
            for row in con.execute(
                """
                SELECT draft_id, MAX(version) AS version, MAX(topic) AS topic,
                       MAX(created_at) AS created_at, MAX(length(content)) AS chars
                FROM writing_history GROUP BY draft_id ORDER BY MAX(created_at) DESC LIMIT 20
                """
            )
        ]
        evaluations = [
            dict(row)
            for row in con.execute(
                "SELECT draft_id, version, score, evaluator, issues_json, created_at FROM evaluation_history ORDER BY id DESC LIMIT 20"
            )
        ]
    finally:
        con.close()
    return {
        "articles": total,
        "avg_text_length": round(avg_len, 1),
        "clusters": clusters,
        "viral_tiers": tiers,
        "viral_labels": labels,
        "knowledge_assets": assets,
        "drafts": drafts,
        "evaluations": evaluations,
    }


def jobs(limit: int = 20) -> dict:
    limit = max(1, min(int(limit), 100))
    system = connect(system_db_path())
    try:
        crawl = [
            dict(row)
            for row in system.execute(
                "SELECT id, source, category, status, attempts, last_error, created_at, finished_at "
                "FROM crawl_jobs ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        ]
        model = [
            dict(row)
            for row in system.execute(
                "SELECT id, job_type, category, status, attempts, last_error, created_at, started_at, finished_at "
                "FROM model_jobs ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        ]
    finally:
        system.close()
    return {"crawl": crawl, "model": model}


def logs(limit: int = 100) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    records: list[dict] = []
    files = sorted(cfg.LOG_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[:3]:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if len(records) >= limit:
                break
            try:
                records.append(json.loads(line))
            except Exception:  # noqa: BLE001 - 跳过损坏行
                continue
        if len(records) >= limit:
            break
    return records


def model_status() -> dict:
    return {"health": model_client.health(), "queue": model_queue.stats()}


# ---------------------------------------------------------------------------
# P11 任务下发:面板只能入队采集 / 触发本地分析,绝不能触发写作、发布或模型调用。


def sources() -> list[dict]:
    from ttxt.crawler import sources as source_loader

    try:
        items = source_loader.load_sources()
    except FileNotFoundError:
        return []
    return [
        {
            "id": item.id,
            "name": item.name,
            "category": item.category,
            "kind": item.kind,
            "enabled": bool(item.enabled),
            "demo": bool(item.demo),
        }
        for item in items
    ]


def task_queue(limit: int = 20) -> dict:
    limit = max(1, min(int(limit), 100))
    system = connect(system_db_path())
    try:
        pending = [
            dict(row)
            for row in system.execute(
                "SELECT id, source, category, status, attempts, max_attempts, created_at "
                "FROM crawl_jobs WHERE status IN ('queued','running') ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        ]
    finally:
        system.close()
    try:
        from ttxt import scheduler

        schedule = scheduler.status_report()
    except Exception as exc:  # noqa: BLE001 - 调度状态缺失不影响任务队列
        schedule = {"error": f"{type(exc).__name__}: {exc}"}
    return {"pending": pending, "schedule": schedule}


def enqueue_crawl(source_id: str, category: str | None = None) -> dict:
    from ttxt.crawler import runner
    from ttxt.crawler import sources as source_loader
    from ttxt.crawler import store as crawler_store

    if not source_id or not str(source_id).strip():
        raise ValueError("必须指定 source_id")
    try:
        source = source_loader.get_source(str(source_id).strip())
    except KeyError as exc:
        raise ValueError(f"来源不存在:{source_id}") from exc
    target = runner.resolve_category(source, (str(category).strip() or None) if category else None)
    existing = crawler_store.find_pending_crawl_job(source.id, target)
    if existing is not None:
        return {"job_id": existing, "category": target, "deduped": True, "note": "已有排队/运行任务,未重复入队"}
    job_id = crawler_store.create_crawl_job(
        source.id, target, meta={"dispatched_via": "dashboard"}
    )
    return {"job_id": job_id, "category": target, "deduped": False, "note": "已入队,等待执行(ttxt crawl-queue --run)"}


def run_analyze_task(category: str) -> dict:
    from ttxt.analysis import analyze

    slug = str(category or "").strip()
    if not slug or not store.SLUG_RE.match(slug):
        raise ValueError(f"非法类目:{category!r}")
    allowed = set(cfg.load_config().get("categories", []))
    if slug not in allowed and not (cfg.CATEGORIES_DIR / slug / "articles.db").exists():
        raise ValueError(f"类目 {slug} 不在配置且无本地库,拒绝分析")
    result = analyze.run_analysis(slug)
    return {
        "ok": True,
        "category": slug,
        "keywords": result["keywords"]["keywords"],
        "zero_coverage": result["keywords"]["zero_coverage"],
        "feedback_gaps": len(result["feedback"]["gap_keywords"]),
        "feedback_job": result["feedback"].get("queued_job_id"),
        "viral_articles": result["viral"].get("articles"),
        "knowledge_assets": result["knowledge"].get("assets"),
    }


# ---------------------------------------------------------------------------
# R59 类目面板(批 A):只读读数 + 写封装。
# 面板上所有数字一律现算:代码里不写死任何文章数/队列数,类目切换后每个数字
# 都来自当次对该类目库的查询。新增读数一律走 SQLite 只读 URI(mode=ro),
# 物理上不可写;写操作只封装既有入口(采集入队 / 补采 run_due / 写作走 CLI 子进程
# 经 model_gate 串行),并有按类目+动作的单飞锁,绝不并发跑两次。


def _ro_connect(path: Path) -> sqlite3.Connection:
    """只读连接:URI mode=ro 强制,任何写尝试都会被 SQLite 拒绝。"""
    con = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    """表是否存在(存在就照实查:列名写错必须报错,不能被 ``except OperationalError`` 吞成空读数)。"""
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _validated_slug(category: str) -> str:
    slug = str(category or "").strip()
    if not slug or not store.SLUG_RE.match(slug):
        raise ValueError(f"非法类目:{category!r}")
    return slug


def _runs_dir() -> Path:
    path = cfg.TEMP_DIR / "dashboard_runs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _lock_path(category: str, action: str) -> Path:
    return _runs_dir() / f"{category}-{action}.lock"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":  # stdlib ctypes,零新依赖
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _acquire_run_lock(category: str, action: str) -> tuple[Path | None, dict]:
    """单飞锁:已被活进程持有 ⇒ (None, holder);成功取得 ⇒ (lock_path, {})。

    锁文件记录 {pid, started_at};持有进程已死 ⇒ 陈旧锁自动回收,无需人工清理。
    """
    lock = _lock_path(category, action)
    for stale_pass in (False, True):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                payload = json.loads(lock.read_text(encoding="utf-8") or "{}")
            except (OSError, ValueError):
                payload = {}
            holder = payload if isinstance(payload, dict) else {}
            if not stale_pass and _pid_alive(int(holder.get("pid") or -1)):
                return None, {
                    "pid": int(holder.get("pid")),
                    "started_at": str(holder.get("started_at") or ""),
                }
            try:
                lock.unlink()
            except OSError:
                pass
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                {"pid": os.getpid(), "started_at": cfg.now_iso(), "category": category, "action": action},
                handle,
            )
        return lock, {}
    return None, {"pid": -1, "started_at": "", "note": "锁文件反复陈旧,未能取得锁"}


def _release_run_lock(lock: Path | None) -> None:
    if lock is None:
        return
    try:
        lock.unlink()
    except OSError:
        pass


def category_metrics(slug: str) -> dict:
    """类目指标面板读数(全部现算,只查该类目库,绝不跨类目聚合)。

    口径:
    - ``articles``:该类目库全部文章;
    - ``measurable_articles``:URL 主机在公开指标白名单(``metrics_pages.host_allowed``)内的篇数,
      即补采真正能补到 T+N 读数的那部分;
    - ``queue``:``metrics_due_queue`` 按 state 计数(该库没跑过 due 队列 ⇒ 空,只读连接不建表);
    - ``pending_backfill``:scheduled+ready+backlog(未拿到读数、待补采);
    - ``failure_outcomes``:``last_outcome`` 各类计数(unsupported_host / article_gone / infra_unavailable 等);
    - ``articles_24h``:``fetched_at`` 落在最近 24 小时(与 ``cfg.now_iso`` 同一时区口径);
    - ``with_metrics_snapshot``:``article_metrics`` 里已有读数行的文章数。
    """
    from ttxt.crawler import metrics_pages

    slug = _validated_slug(slug)
    path = _category_db(slug)
    if path is None:
        return {
            "category": slug,
            "exists": False,
            "articles": 0,
            "measurable_articles": 0,
            "articles_24h": 0,
            "with_metrics_snapshot": 0,
            "queue": {},
            "pending_backfill": 0,
            "failure_outcomes": {},
        }
    con = _ro_connect(path)
    try:
        total = int(con.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
        measurable = sum(
            1
            for row in con.execute("SELECT url, url_normalized FROM articles")
            if metrics_pages.host_allowed(str(row["url_normalized"] or row["url"] or ""))
        )
        cutoff = (datetime.now(timezone.utc).astimezone() - timedelta(hours=24)).isoformat(timespec="seconds")
        recent = int(con.execute("SELECT COUNT(*) FROM articles WHERE fetched_at >= ?", (cutoff,)).fetchone()[0])
        snapshots = (
            int(con.execute("SELECT COUNT(DISTINCT article_id) FROM article_metrics").fetchone()[0])
            if _has_table(con, "article_metrics")
            else 0
        )
        if _has_table(con, "metrics_due_queue"):
            queue = {
                str(row["state"]): int(row["n"])
                for row in con.execute(
                    "SELECT state, COUNT(*) AS n FROM metrics_due_queue WHERE category=? GROUP BY state",
                    (slug,),
                )
            }
            outcomes = {
                str(row["outcome"]): int(row["n"])
                for row in con.execute(
                    "SELECT last_outcome AS outcome, COUNT(*) AS n FROM metrics_due_queue"
                    " WHERE category=? AND last_outcome IS NOT NULL AND last_outcome <> '' GROUP BY last_outcome",
                    (slug,),
                )
            }
        else:
            # 该库没跑过 due 队列:读数如实为空。列名写错则必须报错,不能被 except 吞成空读数
            queue, outcomes = {}, {}
    finally:
        con.close()
    return {
        "category": slug,
        "exists": True,
        "articles": total,
        "measurable_articles": measurable,
        "articles_24h": recent,
        "with_metrics_snapshot": snapshots,
        "queue": queue,
        "pending_backfill": sum(queue.get(state, 0) for state in ("scheduled", "ready", "backlog")),
        "failure_outcomes": outcomes,
    }


def category_jobs(slug: str, limit: int = 20) -> dict:
    """类目相关任务(采集 job + 模型 job + 该类目 due 队列近期尝试),全部现查。"""
    slug = _validated_slug(slug)
    limit = max(1, min(int(limit), 100))
    system = _ro_connect(system_db_path())
    try:
        crawl = [
            dict(row)
            for row in system.execute(
                "SELECT id, source, category, status, attempts, last_error, created_at, finished_at "
                "FROM crawl_jobs WHERE category=? ORDER BY id DESC LIMIT ?",
                (slug, limit),
            )
        ]
        model = [
            dict(row)
            for row in system.execute(
                "SELECT id, job_type, category, status, attempts, last_error, created_at, started_at, finished_at "
                "FROM model_jobs WHERE category=? ORDER BY id DESC LIMIT ?",
                (slug, limit),
            )
        ]
    finally:
        system.close()
    attempts: list[dict] = []
    path = _category_db(slug)
    if path is not None:
        con = _ro_connect(path)
        try:
            if _has_table(con, "metrics_due_queue"):
                # 列名写错必须报错(500 显示真实错误),不能被吞成空表 —— "暂无数据"必须是真没数据
                attempts = [
                    dict(row)
                    for row in con.execute(
                        "SELECT article_id, state, attempts, last_outcome, last_error, last_attempt_at,"
                        " next_attempt_at "
                        "FROM metrics_due_queue WHERE category=? ORDER BY updated_at DESC LIMIT ?",
                        (slug, limit),
                    )
                ]
        finally:
            con.close()
    return {"crawl": crawl, "model": model, "metrics_attempts": attempts}


def run_status(category: str, action: str) -> dict:
    """写操作的运行态:单飞锁在跑 ⇒ running + 已耗时;日志尾部给出真实结束原因。"""
    slug = _validated_slug(category)
    out: dict = {
        "category": slug,
        "action": action,
        "running": False,
        "pid": None,
        "started_at": None,
        "elapsed_s": None,
        "log_tail": "",
    }
    try:
        payload = json.loads(_lock_path(slug, action).read_text(encoding="utf-8") or "{}")
        holder = payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        holder = {}
    pid = int(holder.get("pid") or -1)
    started = str(holder.get("started_at") or "")
    if pid > 0 and _pid_alive(pid):
        out["running"] = True
        out["pid"] = pid
        out["started_at"] = started or None
        try:
            out["elapsed_s"] = round(
                max(0.0, (datetime.now(timezone.utc).astimezone() - datetime.fromisoformat(started)).total_seconds()), 1
            )
        except ValueError:
            pass
    elif started:
        out["started_at"] = started  # 进程已退出:保留开始时间供面板展示,运行态以日志为准
    try:
        text = (_runs_dir() / f"{slug}-{action}.log").read_text(encoding="utf-8", errors="replace")
        out["log_tail"] = text[-4000:]
    except OSError:
        pass
    return out


def category_writing(slug: str) -> dict:
    """写作页签读数:草稿版本链 + 最近评审结论(通过/驳回的真实原因)+ 生成运行态。"""
    slug = _validated_slug(slug)
    status = run_status(slug, "write")
    path = _category_db(slug)
    if path is None:
        return {"category": slug, "drafts": [], "evaluations": [], "status": status}
    con = _ro_connect(path)
    try:
        drafts = [
            dict(row)
            for row in con.execute(
                """
                SELECT draft_id, MAX(version) AS version, MAX(topic) AS topic,
                       MAX(created_at) AS created_at, MAX(length(content)) AS chars
                FROM writing_history GROUP BY draft_id ORDER BY MAX(created_at) DESC LIMIT 20
                """
            )
        ]
        evaluations = []
        for row in con.execute(
            "SELECT draft_id, version, score, evaluator, issues_json, created_at "
            "FROM evaluation_history ORDER BY id DESC LIMIT 10"
        ):
            entry = dict(row)
            try:
                entry["issues"] = json.loads(entry.get("issues_json") or "[]")
            except ValueError:
                entry["issues"] = [str(entry.get("issues_json") or "")]
            evaluations.append(entry)
    finally:
        con.close()
    return {"category": slug, "drafts": drafts, "evaluations": evaluations, "status": status}


def run_metrics_backfill(category: str, *, limit: int = 24, sleep_s: float = 2.5) -> dict:
    """补采(默认 24 篇 / 间隔 2.5s):复用 ``metrics_queue.run_due`` 全部保护(白名单前置、
    下架台账、infra 冷却、attempts 退避),不复制任何口径。单飞锁保证同类目不同时跑两次。"""
    from ttxt.analysis import metrics_queue

    slug = _validated_slug(category)
    if _category_db(slug) is None:
        raise ValueError(f"类目 {slug} 无本地库,拒绝补采")
    lock, holder = _acquire_run_lock(slug, "metrics-backfill")
    if lock is None:
        return {"category": slug, "deduped": True, "running": holder, "note": "该类目的补采已在运行,未重复启动"}
    try:
        report = metrics_queue.run_due(
            slug,
            limit=limit,
            apply=True,
            include_new_articles=True,
            gate_hosts=True,
            collect_kwargs={"sleep_s": sleep_s},
        )
        return {
            "category": slug,
            "deduped": False,
            "skipped": report.get("skipped"),
            "queue_sync": report.get("queue_sync"),
            "infra": report.get("infra"),
            "claimed": report.get("claimed"),
            "collection": report.get("collection"),
            "attempts": report.get("attempts"),
            "infra_failures": report.get("infra_failures"),
            "windows_refreshed": report.get("windows_refreshed"),
            "after": report.get("after"),
        }
    finally:
        _release_run_lock(lock)


def start_write_task(category: str, topic: str) -> dict:
    """写作:起 argv 子进程调 ``python -m ttxt write``(不经 shell、不复制类目注册校验),
    模型调用走既有 model_gate 串行队列;单飞锁保证同类目同一时刻只有一次生成。
    结果与真实失败原因从 ``99_temp/dashboard_runs/<slug>-write.log`` 读。"""
    slug = _validated_slug(category)
    subject = str(topic or "").strip()
    if not subject:
        raise ValueError("topic 不能为空")
    if len(subject) > 300:
        raise ValueError("topic 过长(最多 300 字)")
    lock, holder = _acquire_run_lock(slug, "write")
    if lock is None:
        return {"category": slug, "deduped": True, "running": holder, "note": "该类目已有生成在跑,未重复启动"}
    log_path = _runs_dir() / f"{slug}-write.log"
    try:
        with log_path.open("ab") as log_handle:
            process = subprocess.Popen(  # noqa: S603 - argv 列表、无 shell,参数已校验
                [sys.executable, "-m", "ttxt", "write", "--category", slug, "--topic", subject],
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                cwd=str(cfg.PROJECT_ROOT),
            )
        lock.write_text(
            json.dumps(
                {"pid": process.pid, "started_at": cfg.now_iso(), "category": slug, "action": "write"},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        _release_run_lock(lock)
        raise
    return {
        "category": slug,
        "started": True,
        "deduped": False,
        "pid": process.pid,
        "log": str(log_path),
        "note": "生成走 model_gate 串行队列;同类目面板保证同一时刻只有一次生成",
    }
