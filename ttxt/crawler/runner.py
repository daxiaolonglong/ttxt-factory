"""采集执行器:robots 检查 → 限速抓取 → 抽取 → 去重入库 → 任务记录。"""

from __future__ import annotations

import time
from typing import Any

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


def run_queued(limit: int = 5, category: str | None = None) -> list[dict[str, Any]]:
    """消费 queued 采集任务(面板/调度器只入队,由本入口或人工统一执行)。"""
    jobs = store.list_queued_crawl_jobs(limit=limit, category=category)
    results: list[dict[str, Any]] = []
    for job in jobs:
        job_id = int(job["id"])
        source_id = str(job["source"])
        try:
            source = sources.get_source(source_id)
        except KeyError:
            store.finish_crawl_job(job_id, "failed", f"来源不存在:{source_id}")
            results.append({"job_id": job_id, "source": source_id, "ok": False, "error": "source_missing"})
            continue
        if not source.enabled:
            store.finish_crawl_job(job_id, "failed", f"来源已停用:{source_id}")
            results.append({"job_id": job_id, "source": source_id, "ok": False, "error": "source_disabled"})
            continue
        try:
            target_category = resolve_category(source, str(job["category"]) or None)
        except ValueError as exc:
            store.finish_crawl_job(job_id, "failed", f"类目路由拒绝:{exc}")
            results.append({"job_id": job_id, "source": source_id, "ok": False, "error": str(exc)})
            continue
        store.mark_job_started(job_id)
        results.append(_execute_job(job_id, source, target_category))
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
        "items_extracted": 0,
        "inserted": 0,
        "duplicates": 0,
        "elapsed_s": 0.0,
        "error": None,
    }
    try:
        ua = str(cfg.load_config().get("crawler", {}).get("user_agent", "TouTiaoSmartFactory/0.1"))
        rules = robots.fetch_robots(source.url, ua)
        if rules.unknown:
            store.finish_crawl_job(
                job_id, "failed", "robots.txt 无法确认(5xx/网络异常),来源暂停,不放行", meta=summary
            )
            summary["error"] = "robots_unknown"
            LOG.error("crawl.robots_unknown", source=source.id)
            summary["elapsed_s"] = round(time.monotonic() - started, 2)
            return summary
        path = robots.target_path(source.url)
        summary["robots_allowed"] = rules.allowed(path)
        if not rules.allowed(path):
            store.finish_crawl_job(job_id, "failed", "robots.txt disallows this path", meta=summary)
            summary["error"] = "robots_disallow"
            LOG.warn("crawl.robots_disallow", source=source.id, path=path)
            summary["elapsed_s"] = round(time.monotonic() - started, 2)
            return summary

        delay = max(float(source.rate_limit_s), float(rules.crawl_delay_s or 0))
        items: list[extract.ExtractedItem] = []

        if source.kind == "rss":
            result = fetcher.fetch_page(source.url)
            summary["pages_fetched"] += 1
            items = extract.extract_rss_items(fetcher.page_text(result.page), source)
            summary["items_extracted"] = len(items)
        elif source.extractor == "toutiao":
            from . import toutiao as toutiao_adapter

            collected = toutiao_adapter.collect(source, limit=limit)
            items = collected["items"]
            summary["items_extracted"] = len(items)
            summary["listing_links"] = collected["links"]
            summary["robots_skipped"] = collected["robots_skipped"]
            summary["article_errors"] = collected["errors"]
        elif source.kind == "html":
            pages = 0
            url: str | None = source.url
            page_cap = max_pages or source.max_pages
            while url and pages < page_cap:
                result = fetcher.fetch_page(url)
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
                if not next_url:
                    break
                time.sleep(delay)
                url = next_url
        else:
            raise RuntimeError(f"unsupported source kind: {source.kind}")

        if limit:
            items = items[:limit]
        summary["items_extracted"] = len(items)
        counts = store.insert_items(target_category, source.id, items)
        summary.update(counts)
        summary["ok"] = True
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
        outcome = store.fail_crawl_job(job_id, summary["error"], meta=summary)
        summary["requeued"] = outcome == "queued"
        LOG.error("crawl.failed", source=source.id, error=summary["error"], outcome=outcome)
    finally:
        summary["elapsed_s"] = round(time.monotonic() - started, 2)
    return summary
