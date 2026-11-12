"""采集执行器:robots 检查 → 限速抓取 → 抽取 → 去重入库 → 任务记录。"""

from __future__ import annotations

import time
from typing import Any

from ttxt import config as cfg
from ttxt.logging import get_logger

from . import clean, extract, fetcher, robots, sources, store

LOG = get_logger("crawler.runner")


def crawl_source(
    source_id: str,
    category: str | None = None,
    limit: int | None = None,
    max_pages: int | None = None,
) -> dict[str, Any]:
    source = sources.get_source(source_id)
    if not source.enabled:
        raise RuntimeError(f"source disabled: {source_id}")
    target_category = category or source.category
    store.init_articles_db(target_category)
    job_id = store.create_crawl_job(source_id, target_category)
    store.mark_job_started(job_id)

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
    except Exception as exc:  # noqa: BLE001 - 记录根因后返回
        summary["error"] = f"{type(exc).__name__}: {exc}"
        store.finish_crawl_job(job_id, "failed", summary["error"], meta=summary)
        LOG.error("crawl.failed", source=source.id, error=summary["error"])
    finally:
        summary["elapsed_s"] = round(time.monotonic() - started, 2)
    return summary
