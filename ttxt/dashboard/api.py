"""面板数据接口:全部来自真实 SQLite 与结构化日志,不提供模拟数据。"""

from __future__ import annotations

import json
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
                "SELECT draft_id, version, score, issues_json, created_at FROM evaluation_history ORDER BY id DESC LIMIT 20"
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
