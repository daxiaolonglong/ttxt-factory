"""采集执行器:robots 检查 → 限速抓取 → 抽取 → 去重入库 → 任务记录。"""

from __future__ import annotations

import time
from contextlib import nullcontext
from typing import Any
from urllib.parse import urlsplit

from ttxt import config as cfg
from ttxt.logging import get_logger

from . import clean, extract, fetcher, robots, sources, store

LOG = get_logger("crawler.runner")


def resolve_category(source: sources.Source, category: str | None) -> str:
    """来源-类目路由校验。

    - 演示来源(demo=True)只能写入 demo 命名空间;未指定时默认 demo。
    - 正式来源必须写入其声明类目 source.category。
    违反即抛 ValueError,不产生任何数据写入。
    """
    if source.demo:
        if category is None:
            return "demo"
        if category != "demo":
            raise ValueError(
                f"演示来源 {source.id} 只能写入 demo 命名空间,拒绝写入 {category}"
            )
        return "demo"
    if category is None:
        return source.category
    if category != source.category:
        raise ValueError(
            f"来源 {source.id} 声明类目为 {source.category},拒绝写入 {category}"
        )
    return category


def crawl_source(
    source_id: str,
    category: str | None = None,
    limit: int | None = None,
    max_pages: int | None = None,
) -> dict[str, Any]:
    source = sources.get_source(source_id)
    if not source.enabled:
        raise RuntimeError(f"source disabled: {source_id}")
    target_category = resolve_category(source, category)
    store.init_articles_db(target_category)
    job_id = store.create_crawl_job(source_id, target_category)
    store.mark_job_started(job_id)
    return _execute_job(job_id, source, target_category, limit=limit, max_pages=max_pages)


def run_next(
    owner: str = "manual-cli",
    category: str | None = None,
    lease_s: float = 900.0,
) -> dict[str, Any] | None:
    """P12:原子认领并执行一个 queued 任务;无可认领任务时返回 ``None``。

    认领(``store.claim_next_crawl_job``)是进入 running 的唯一通道:写入
    owner/租约、同源不并发、attempts 在认领时 +1 由 ``max_attempts`` 真正封顶。
    人工 CLI 与 Worker 共用本通道,两个执行者不可能同时拿到同一个任务。
    """
    job = store.claim_next_crawl_job(owner, lease_s=lease_s, category=category)
    if job is None:
        return None
    job_id = int(job["id"])
    source_id = str(job["source"])
    try:
        source = sources.get_source(source_id)
    except KeyError:
        # P12:配置性失败 → 终态 aborted(重试无意义;不进 requeue,杜绝幽灵任务无限重排)
        store.abort_crawl_job(job_id, f"来源不存在:{source_id}", meta={"reason": "source_missing"})
        return {"job_id": job_id, "source": source_id, "ok": False, "error": "source_missing"}
    if not source.enabled:
        store.abort_crawl_job(job_id, f"来源已停用:{source_id}", meta={"reason": "source_disabled"})
        return {"job_id": job_id, "source": source_id, "ok": False, "error": "source_disabled"}
    try:
        target_category = resolve_category(source, str(job["category"] or "") or None)
    except ValueError as exc:
        store.abort_crawl_job(job_id, f"类目路由拒绝:{exc}", meta={"reason": "category_refused"})
        return {"job_id": job_id, "source": source_id, "ok": False, "error": str(exc)}
    return _execute_job(job_id, source, target_category)


def run_queued(limit: int = 5, category: str | None = None) -> list[dict[str, Any]]:
    """消费 queued 采集任务(面板/调度器只入队,由本入口、人工或 Worker 统一执行)。"""
    results: list[dict[str, Any]] = []
    for _ in range(max(1, int(limit))):
        summary = run_next(category=category)
        if summary is None:
            break
        results.append(summary)
    return results


def _execute_job(
    job_id: int,
    source: sources.Source,
    target_category: str,
    limit: int | None = None,
    max_pages: int | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    summary: dict[str, Any] = {
        "job_id": job_id,
        "source": source.id,
        "category": target_category,
        "kind": source.kind,
        "ok": False,
        "robots_allowed": None,
        "pages_fetched": 0,
        "robots_skipped": 0,
        "pagination_stop": None,
        "items_extracted": 0,
        "inserted": 0,
        "duplicates": 0,
        "elapsed_s": 0.0,
        "error": None,
    }
    try:
        ua = str(cfg.load_config().get("crawler", {}).get("user_agent", "TouTiaoSmartFactory/0.1"))
        # P12:host 级规则缓存;列表/分页得到的每个新 URL 都必须再过一遍 robots 门禁
        rules_cache: dict[str, robots.RobotsRules] = {}
        rules, first_allowed = robots.gate(source.url, ua, rules_cache)
        if rules.unknown:
            # P12批次3:robots 无法确认是 transient(5xx/网络),必须走可重试通道
            # (真实缺陷 2026-11-12 job 26:who_cn_news robots.txt 瞬时抓取失败,
            # finish_crawl_job 直接钉 failed,attempts 1/3 却不再排队)
            outcome = store.fail_crawl_job(
                job_id, "robots.txt 无法确认(5xx/网络异常),来源暂停,不放行", meta=summary
            )
            summary["error"] = "robots_unknown"
            summary["requeued"] = outcome == "queued"
            LOG.error("crawl.robots_unknown", source=source.id, outcome=outcome)
            summary["elapsed_s"] = round(time.monotonic() - started, 2)
            return summary
        path = robots.target_path(source.url)
        summary["robots_allowed"] = first_allowed
        if not first_allowed:
            store.finish_crawl_job(job_id, "failed", "robots.txt disallows this path", meta=summary)
            summary["error"] = "robots_disallow"
            LOG.warn("crawl.robots_disallow", source=source.id, path=path)
            summary["elapsed_s"] = round(time.monotonic() - started, 2)
            return summary

        delay = max(float(source.rate_limit_s), float(rules.crawl_delay_s or 0))
        items: list[extract.ExtractedItem] = []
        render = source.render == "browser"
        # P12:需要渲染的来源一次任务只开一个浏览器会话(引用计数共享,页面池复用)
        session_cm = fetcher.browser_session() if render else nullcontext(None)
        with session_cm as browser_session:
            if source.kind == "rss":
                result = fetcher.fetch_page(source.url, render=render, session=browser_session)
                summary["pages_fetched"] += 1
                items = extract.extract_rss_items(fetcher.page_text(result.page), source)
                summary["items_extracted"] = len(items)
            elif source.extractor == "toutiao":
                from . import toutiao as toutiao_adapter

                collected = toutiao_adapter.collect(
                    source, limit=limit, rules=rules, session=browser_session
                )
                items = collected["items"]
                summary["items_extracted"] = len(items)
                summary["pages_fetched"] = collected["pages"]
                summary["listing_links"] = collected["links"]
                summary["robots_skipped"] = collected["robots_skipped"]
                summary["article_errors"] = collected["errors"]
            elif source.kind == "html":
                pages = 0
                url: str | None = source.url
                page_cap = max_pages or source.max_pages
                seen: set[str] = set()
                while url and pages < page_cap:
                    norm = clean.normalize_url(url, base=source.url)
                    if norm in seen:
                        summary["pagination_stop"] = "loop"
                        LOG.warn("crawl.pagination_loop", source=source.id, url=url)
                        break
                    seen.add(norm)
                    # P12:分页跳转得到的 URL 同样必须过 robots 门禁(host 级缓存,不重复取规则)
                    _, page_allowed = robots.gate(url, ua, rules_cache)
                    if not page_allowed:
                        summary["robots_skipped"] = int(summary.get("robots_skipped") or 0) + 1
                        summary["pagination_stop"] = "robots"
                        LOG.warn("crawl.pagination_robots", source=source.id, url=url)
                        break
                    result = fetcher.fetch_page(url, render=render, session=browser_session)
                    summary["pages_fetched"] += 1
                    pages += 1
                    items.extend(extract.extract_html_items(result.page, source))
                    summary["items_extracted"] = len(items)
                    if limit and len(items) >= limit:
                        break
                    next_url = None
                    if source.next_selector:
                        found = result.page.css(source.next_selector)
                        if len(found):
                            href = found[0].attrib.get("href")
                            if href:
                                next_url = clean.normalize_url(href, base=url)
                    # P12:仅允许同 host 分页;跨域跳转记录原因并停止(白名单与 robots 均按来源 host 约束)
                    if next_url and urlsplit(next_url).netloc.lower() != urlsplit(source.url).netloc.lower():
                        summary["pagination_stop"] = "cross_host"
                        LOG.warn("crawl.pagination_cross_host", source=source.id, next_url=next_url)
                        next_url = None
                    url = next_url
                    if url:
                        time.sleep(delay)
            else:
                raise RuntimeError(f"unsupported source kind: {source.kind}")

        if limit:
            items = items[:limit]
        elif source.max_articles > 0:
            # P12批次3:RSS/HTML 全量 feed 可达数百条,未显式 limit 时按来源配置封顶
            summary["feed_total"] = len(items)
            items = items[: max(1, int(source.max_articles))]
        summary["items_extracted"] = len(items)
        counts = store.insert_items(target_category, source.id, items)
        summary.update(counts)
        summary["ok"] = True
        # elapsed 必须在落库前算好:否则 meta_json 里永远是初始 0.0,观测谎报
        summary["elapsed_s"] = round(time.monotonic() - started, 2)
        store.finish_crawl_job(job_id, "done", meta=summary)
        LOG.info(
            "crawl.done",
            source=source.id,
            category=target_category,
            extracted=summary["items_extracted"],
            inserted=summary["inserted"],
            duplicates=summary["duplicates"],
        )
    except Exception as exc:  # noqa: BLE001 - 记录根因后按重试策略处理
        summary["error"] = f"{type(exc).__name__}: {exc}"
        summary["elapsed_s"] = round(time.monotonic() - started, 2)
        outcome = store.fail_crawl_job(job_id, summary["error"], meta=summary)
        summary["requeued"] = outcome == "queued"
        LOG.error("crawl.failed", source=source.id, error=summary["error"], outcome=outcome)
    finally:
        summary["elapsed_s"] = round(time.monotonic() - started, 2)
    return summary
