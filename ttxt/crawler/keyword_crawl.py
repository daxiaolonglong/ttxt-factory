"""P19(2026-11-15):关键词驱动采集计划——把关键词变成**采集动作**,不只是"反哺入队"。

历史缺陷(根因):``analysis/keywords.py:auto_feedback`` 只做两件事——把零覆盖关键词
标成 ``gap``,然后给该类目 **第一个启用来源**(``:154``)入一条普通采集任务。
关键词本身从来没有进入采集动作:入队任务抓的还是同一个固定地址,
所以"关键词不覆盖 → 去补采"这条闭环在采集侧断掉(实测:health/technology 的 gap 词
补采入队后,采集结果与关键词毫无关系)。

本模块补上这一环,边界如下:

1. **关键词来自类目库**(``keywords`` + ``keyword_performance``,只读):
   ``gap``(零覆盖)词优先,其余按 ``score`` 从高到低 —— 这是"什么值得再采"的系统记忆。
2. **地址必须合规**:只有 ``robots.txt`` 放行的地址才进入计划。
   实测(``07_tmp/p19_keyword_crawl/probe1.json``/``probe2.json``,2026-11-15)百度新闻、
   360 新闻、搜狗新闻、头条搜索、Google News RSS 全部 ``Disallow``,
   因此这些站点**不得**配进 ``url_template``;不是能力问题,是访问控制问题。
   计划阶段就查 robots,不合规的地址当场拒绝并记录原因,不产生注定失败的采集任务。
3. **只入队,不执行**(与面板/调度器同一做法):执行仍走 ``runner.run_queued``,
   计划参数存在 ``crawl_jobs.meta_json``,由 ``store.crawl_job_params`` 读出交给 runner。
4. 每次计划都落证据文件到 ``03_intelligence/keyword_crawl/``,可审计、可复盘。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from ttxt import config as cfg
from ttxt.db import connect
from ttxt.logging import get_logger

from . import robots, sources as source_loader, store

LOG = get_logger("crawler.keyword_crawl")

DEFAULT_KEYWORD_LIMIT = 6
EVIDENCE_SUBDIR = "keyword_crawl"


def category_keywords(
    category: str,
    db_path: Path | None = None,
    limit: int = DEFAULT_KEYWORD_LIMIT,
) -> list[dict[str, Any]]:
    """类目库里这一轮要用的关键词(只读):``gap`` 优先,其余按分数从高到低。

    库不存在时返回空列表,不创建任何文件(类目隔离:未建库的类目没有语料,也没有关键词)。

    R57(2026-11-20):``weak``(仅正文命中的宽词)不进选词——高分表的准入看区分度,
    不看频次;判定闸(runner 入库侧)照旧放行 title∪正文,本处只收窄"什么值得再采"的计划词。
    """
    target = Path(db_path) if db_path is not None else cfg.CATEGORIES_DIR / category / "articles.db"
    if not target.exists():
        return []
    con = connect(target)
    try:
        rows = con.execute(
            "SELECT k.keyword, k.status, COALESCE(p.score, 0) AS score, COALESCE(p.collected, 0) AS collected"
            " FROM keywords k LEFT JOIN keyword_performance p ON p.keyword_id = k.id"
            " WHERE k.category = ? AND k.status <> 'weak'"
            " ORDER BY CASE k.status WHEN 'gap' THEN 0 ELSE 1 END, score DESC, k.id"
            " LIMIT ?",
            (category, max(1, int(limit))),
        ).fetchall()
    finally:
        con.close()
    return [
        {
            "keyword": str(row["keyword"]),
            "status": str(row["status"]),
            "score": float(row["score"] or 0.0),
            "collected": int(row["collected"] or 0),
        }
        for row in rows
    ]


def _same_host(left: str, right: str) -> bool:
    return urlsplit(left).netloc.lower() == urlsplit(right).netloc.lower()


def article_count(category: str, db_path: Path | None = None) -> int:
    """类目库现有文章数(只读)。库不存在返回 0 —— 不创建任何文件。"""
    target = Path(db_path) if db_path is not None else cfg.CATEGORIES_DIR / category / "articles.db"
    if not target.exists():
        return 0
    con = connect(target)
    try:
        return int(con.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
    finally:
        con.close()


def plan_category(
    category: str,
    keywords: list[str] | None = None,
    source_ids: list[str] | None = None,
    keyword_limit: int = DEFAULT_KEYWORD_LIMIT,
    sources_path: Path | None = None,
    db_path: Path | None = None,
    user_agent: str | None = None,
    check_robots: bool = True,
) -> dict[str, Any]:
    """为类目里每个启用正式来源生成采集计划(**只出计划,不入库、不入队**)。

    两种落地方式,按来源能力自动选:

    - 来源配了 ``url_template``(含 ``{keyword}``):每个关键词一条计划,
      ``url_override`` 指向检索页;必须同 host(跨 host 计划当场拒绝)且 robots 放行。
    - 没配 ``url_template``:一条计划带 ``keyword_filter``,采回来只入库命中关键词的条目
      (``runner`` 入库侧闸门,计数 ``keyword_filtered_out``,不静默丢弃)。
      正文短的来源再配 ``fetch_item_pages`` 把整篇正文补回来。
    """
    loader_kwargs: dict[str, Any] = {"path": Path(sources_path)} if sources_path else {}
    all_sources = source_loader.load_sources(**loader_kwargs)
    wanted = [
        item
        for item in all_sources
        if item.category == category and item.enabled and not item.demo and (not source_ids or item.id in source_ids)
    ]
    chosen = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    keyword_rows = category_keywords(category, db_path=db_path, limit=keyword_limit)
    if not chosen:
        chosen = [row["keyword"] for row in keyword_rows]
    ua = user_agent or str(cfg.load_config().get("crawler", {}).get("user_agent", "TouTiaoSmartFactory/0.1"))
    articles = article_count(category, db_path=db_path)
    cold_start = articles == 0

    plans: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    rules_cache: dict[str, robots.RobotsRules] = {}
    for source in wanted:
        own = [str(k).strip() for k in (source.keywords or []) if str(k).strip()] or chosen[: max(1, int(keyword_limit))]
        if source.url_template and "{keyword}" in source.url_template:
            for keyword in own:
                url = source.url_template.replace("{keyword}", quote(keyword))
                if not _same_host(url, source.url):
                    rejected.append({"source": source.id, "keyword": keyword, "url": url, "reason": "cross_host"})
                    continue
                if check_robots:
                    rules, allowed = robots.gate(url, ua, rules_cache)
                    if rules.unknown or not allowed:
                        rejected.append(
                            {
                                "source": source.id,
                                "keyword": keyword,
                                "url": url,
                                "reason": "robots_unknown" if rules.unknown else "robots_disallow",
                            }
                        )
                        continue
                plans.append(
                    {
                        "source": source.id,
                        "category": category,
                        "keyword": keyword,
                        "url_override": url,
                        "reason": "keyword_search",
                        "keywords": [keyword],
                    }
                )
        else:
            if cold_start:
                # 类目库为空时开关键词闸门是自伤:闸门只会把语料卡成 0 条,而空类目最缺的
                # 恰恰是语料本身(实测:social/history 各 0 行,种子词又都是宽词)。
                # 冷启动先整源采集结语料,让 auto_feedback 从真实语料长出有覆盖的词,
                # 之后闸门再开,卡掉的才是真不相关的条目。
                plans.append(
                    {
                        "source": source.id,
                        "category": category,
                        "keywords": own,
                        "reason": "cold_start",
                        "cold_start": True,
                        "fetch_item_pages": bool(source.fetch_item_pages),
                        "note": "类目库为空:本轮不设关键词闸门,先采集结语料",
                    }
                )
            else:
                plans.append(
                    {
                        "source": source.id,
                        "category": category,
                        "keyword_filter": own,
                        "keywords": own,
                        "reason": "keyword_filter",
                        "fetch_item_pages": bool(source.fetch_item_pages),
                    }
                )
    return {
        "category": category,
        "generated_at": cfg.now_iso(),
        "keywords": chosen,
        "keyword_rows": keyword_rows,
        "articles_before": articles,
        "cold_start": cold_start,
        "sources": [item.id for item in wanted],
        "plans": plans,
        "rejected": rejected,
        "note": "只出计划;加 --apply 才入队,入队后仍需 crawl-queue --run 或 Worker 执行",
    }


def enqueue_plan(plan: dict[str, Any], system_db: Path | None = None) -> dict[str, Any]:
    """把计划变成 queued 采集任务(同来源+类目已有排队/运行任务则复用,不重复入队)。"""
    queued = 0
    reused = 0
    for entry in plan.get("plans", []):
        if entry.get("job_id"):
            continue
        existing = store.find_pending_crawl_job(entry["source"], entry["category"], db_path=system_db)
        if existing is not None:
            entry["job_id"] = existing
            entry["note"] = "已有排队/运行任务,本次复用"
            reused += 1
            continue
        meta = {
            "reason": entry.get("reason") or "keyword_plan",
            "dispatched_via": "keyword_plan",
            "keywords": entry.get("keywords") or [],
        }
        if entry.get("keyword_filter"):
            meta["keyword_filter"] = entry["keyword_filter"]
        if entry.get("url_override"):
            meta["url_override"] = entry["url_override"]
        if entry.get("keyword"):
            meta["keyword"] = entry["keyword"]
        entry["job_id"] = store.create_crawl_job(entry["source"], entry["category"], db_path=system_db, meta=meta)
        queued += 1
    plan["queued_jobs"] = queued
    plan["reused_jobs"] = reused
    return plan


def plan_and_enqueue(
    category: str,
    keywords: list[str] | None = None,
    source_ids: list[str] | None = None,
    keyword_limit: int = DEFAULT_KEYWORD_LIMIT,
    apply: bool = False,
    sources_path: Path | None = None,
    db_path: Path | None = None,
    system_db: Path | None = None,
    evidence_dir: Path | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """出计划 →(可选)入队 → 落证据文件。``apply=False`` 时不写任务表(默认干跑)。"""
    plan = plan_category(
        category,
        keywords=keywords,
        source_ids=source_ids,
        keyword_limit=keyword_limit,
        sources_path=sources_path,
        db_path=db_path,
        user_agent=user_agent,
    )
    plan["applied"] = bool(apply)
    if apply:
        enqueue_plan(plan, system_db=system_db)
    out_dir = Path(evidence_dir) if evidence_dir else cfg.INTELLIGENCE_DIR / EVIDENCE_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = cfg.now_iso().replace(":", "").replace("-", "")
    out_path = out_dir / f"{category}-plan-{stamp}.json"
    out_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    plan["evidence_file"] = str(out_path)
    LOG.info(
        "keyword_crawl.planned",
        category=category,
        keywords=len(plan["keywords"]),
        plans=len(plan["plans"]),
        rejected=len(plan["rejected"]),
        queued=plan.get("queued_jobs", 0),
        applied=plan["applied"],
    )
    return plan
