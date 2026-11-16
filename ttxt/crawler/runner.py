"""采集执行器:robots 检查 → 限速抓取 → 抽取 → 去重入库 → 任务记录。"""

from __future__ import annotations

import dataclasses
import time
from contextlib import nullcontext
from typing import Any
from urllib.parse import urlsplit

from ttxt import config as cfg
from ttxt.logging import get_logger

from . import clean, extract, fetcher, pipeline, robots, sources, store

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
    # P19:入队时写进 meta 的采集计划(关键词/检索地址)只有在这里读才拿得到:
    # finish_crawl_job 会用执行回执整体覆盖 meta_json
    params = store.crawl_job_params(job_id)
    return _execute_job(job_id, source, target_category, params=params)


def run_queued(limit: int = 5, category: str | None = None) -> list[dict[str, Any]]:
    """消费 queued 采集任务(面板/调度器只入队,由本入口、人工或 Worker 统一执行)。"""
    results: list[dict[str, Any]] = []
    for _ in range(max(1, int(limit))):
        summary = run_next(category=category)
        if summary is None:
            break
        results.append(summary)
    return results


def _enrich_item_bodies(
    items: list[extract.ExtractedItem],
    source: sources.Source,
    page_url: str,
    ua: str,
    rules_cache: dict[str, robots.RobotsRules],
    delay: float,
    summary: dict[str, Any],
    browser_session: Any = None,
) -> None:
    """P19:逐条抓文章页补正文(原地改写 ``items``,并写计数字段)。

    为什么必须有这一步(实测证据 ``07_tmp/p19_keyword_crawl/probe2.json``/``probe3.json``):
    中新网 RSS 摘要中位数只有 71–108 字符,澎湃列表页只给标题——不补正文,
    库里存的就只是"标题 + 一句摘要",爆款研究没有正文可学。

    安全与边界:
    - 每一个文章页 URL **单独过 robots 门禁**(host 级缓存,不重复取规则);
      被拒绝或无法确认 ⇒ 跳过并计数,绝不换 UA、绝不重试到放行。
    - 只补 ``max_item_pages`` 条,任务时间与磁盘有界。
    - 抓不到正文 ⇒ 保留原来的摘要(缺失好过编造),错误计数 ``item_page_errors``。
    - 全部尝试都失败且这些条目原本就没有像样正文 ⇒ 抛异常走可重试通道,
      与 toutiao 适配器"全损必须重试"(runner:181)同一判据;部分失败仍算完成。
    """
    fetched = enriched = errors = robots_skipped = no_own_url = capped = 0
    weak_attempted = 0
    attempted: set[str] = set()
    cap = max(1, int(source.max_item_pages or 1))
    for item in items:
        if len(attempted) >= cap:
            capped = 1
            break
        url = str(item.url or "").strip()
        if item.url_is_page_fallback or not url or url == page_url:
            no_own_url += 1
            continue
        rules, allowed = robots.gate(url, ua, rules_cache)
        if not allowed:
            robots_skipped += 1
            LOG.warn("crawl.item_page_robots", source=source.id, url=url, unknown=rules.unknown)
            continue
        attempted.add(url)
        if len(item.content or "") < extract.MIN_BODY_CHARS:
            weak_attempted += 1
        time.sleep(delay)
        try:
            result = fetcher.fetch_page(url, render=source.render == "browser", session=browser_session)
        except Exception as exc:  # noqa: BLE001 - 单条失败不得毁掉整批,保留原摘要并计数
            errors += 1
            LOG.warn("crawl.item_page_failed", source=source.id, url=url, error=f"{type(exc).__name__}: {exc}"[:200])
            continue
        fetched += 1
        body = extract.extract_article_body(result.page, source)
        if body and len(body) > len(item.content or ""):
            item.content = body
            item.excerpt = extract.excerpt(body)
            enriched += 1
    summary["item_pages_fetched"] = fetched
    summary["items_enriched"] = enriched
    summary["item_page_errors"] = errors
    summary["item_robots_skipped"] = robots_skipped
    summary["items_no_own_url"] = no_own_url
    if capped:
        summary["item_pages_capped"] = cap
        LOG.warn(
            "crawl.item_pages_capped",
            source=source.id,
            budget=cap,
            items=len(items),
            detail=(
                f"来源 {source.id} 的正文补全预算 {cap} 小于本轮条目数 {len(items)},"
                f"其余 {len(items) - cap} 条只能以摘要级正文入库(completeness=short)。"
                "整改:把 max_item_pages 提到 ≥ max_articles,或降低 max_articles ——"
                "明知这一轮能补正文却先入库摘要级语料,等于把语料质量问题推给以后"
            ),
        )
    if attempted and enriched == 0 and errors >= len(attempted) and weak_attempted:
        summary["item_pages_all_failed"] = True
        raise RuntimeError(
            f"来源 {source.id} 的文章页正文补全全部失败({errors}/{len(attempted)}),"
            "入库只会得到摘要级语料,走可重试通道"
        )


def _execute_job(
    job_id: int,
    source: sources.Source,
    target_category: str,
    limit: int | None = None,
    max_pages: int | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    params = params or {}

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
        # P19(2026-11-15):关键词驱动采集。计划阶段的 keyword_crawl 会把"这一轮实际要抓的地址"
        # 写进入队 meta;这里才生效。url_override **只允许与来源同 host**:
        # 跨域改写会让一个来源去采别人的站点,类目路由、host 级 robots 缓存和
        # C-3 跨库判定的依据全部失效(与 P12 分页跨域停止同一判据)。
        effective_url = str(source.url)
        override = str(params.get("url_override") or "").strip()
        if override:
            if urlsplit(override).netloc.lower() != urlsplit(source.url).netloc.lower():
                summary["url_override_rejected"] = "cross_host"
                LOG.warn("crawl.url_override_cross_host", source=source.id, override=override, base=source.url)
            else:
                effective_url = override
        # 抽取器/入库侧一律按"这一轮实际抓的地址"工作:RSS 相对链接绝对化、
        # 列表页回退地址、page_urls 记账都用它,不然它们会指向来源默认页(P19 实测缺陷方向)
        job_source = dataclasses.replace(source, url=effective_url) if effective_url != source.url else source
        if params.get("keywords") or params.get("keyword_filter") or override:
            # 计划回显进执行回执:meta_json 会被 finish_crawl_job 覆盖,不回显就查不到这一轮为什么采
            summary["plan"] = {
                "keywords": list(params.get("keywords") or params.get("keyword_filter") or []),
                "url_override": override or None,
                "reason": str(params.get("reason") or "keyword_crawl"),
            }
        # P12:host 级规则缓存;列表/分页得到的每个新 URL 都必须再过一遍 robots 门禁
        rules_cache: dict[str, robots.RobotsRules] = {}
        rules, first_allowed = robots.gate(effective_url, ua, rules_cache)
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
        path = robots.target_path(effective_url)
        summary["robots_allowed"] = first_allowed
        if not first_allowed:
            store.finish_crawl_job(job_id, "failed", "robots.txt disallows this path", meta=summary)
            summary["error"] = "robots_disallow"
            LOG.warn("crawl.robots_disallow", source=source.id, path=path)
            summary["elapsed_s"] = round(time.monotonic() - started, 2)
            return summary

        delay = max(float(source.rate_limit_s), float(rules.crawl_delay_s or 0))
        items: list[extract.ExtractedItem] = []
        # C-7b:本任务实际抓过的每一页地址。它们一律不是文章身份,必须整批告知入库侧
        # (只传 source.url 会把第 2/3 页漏掉,那些页的回退条目就会被按 URL 误判重复)。
        listing_pages: list[str] = []
        render = source.render == "browser"
        # P12:需要渲染的来源一次任务只开一个浏览器会话(引用计数共享,页面池复用)
        session_cm = fetcher.browser_session() if render else nullcontext(None)
        with session_cm as browser_session:
            if source.kind == "rss":
                result = fetcher.fetch_page(effective_url, render=render, session=browser_session)
                summary["pages_fetched"] += 1
                # P19:订阅地址是"来源页",不是文章身份;C-7b 之后必须整批告知入库侧
                listing_pages.append(effective_url)
                items = extract.extract_rss_items(fetcher.page_text(result.page), job_source)
                summary["items_extracted"] = len(items)
            elif source.extractor == "toutiao":
                from . import toutiao as toutiao_adapter

                collected = toutiao_adapter.collect(
                    job_source, limit=limit, rules=rules, session=browser_session
                )
                items = collected["items"]
                summary["items_extracted"] = len(items)
                summary["pages_fetched"] = collected["pages"]
                summary["listing_links"] = collected["links"]
                summary["robots_skipped"] = collected["robots_skipped"]
                summary["article_errors"] = collected["errors"]
                # 真实缺陷(2026-11-13 job 47,H 场景强杀浏览器):10/10 文章页全部
                # 抓取失败、0 条产出,任务却 ok=True/done/inserted=0 —— 崩溃被吞掉,
                # 永远进不了重试/租约回收通道。全损必须走可重试通道;部分失败仍算完成。
                if (
                    int(collected["links"] or 0) > 0
                    and not items
                    and int(collected["errors"] or 0) >= int(collected["links"] or 0)
                ):
                    raise RuntimeError(
                        f"toutiao 全部文章页抓取失败({collected['errors']}/{collected['links']}),"
                        "疑似浏览器会话崩溃,走可重试通道"
                    )
                if int(collected["errors"] or 0) > 0:
                    LOG.warn(
                        "crawl.article_errors",
                        source=source.id,
                        errors=collected["errors"],
                        links=collected["links"],
                    )
            elif source.kind == "html":
                pages = 0
                url: str | None = effective_url
                page_cap = max_pages or source.max_pages
                seen: set[str] = set()
                while url and pages < page_cap:
                    norm = clean.normalize_url(url, base=effective_url)
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
                    listing_pages.append(str(url))
                    # C-7b:逐条链接按**当前这一页**的地址绝对化。用 source.url 时,
                    # 第 2/3 页的相对链接会指错页,等于拿不到"这一条自己的文章地址"
                    items.extend(extract.extract_html_items(result.page, job_source, page_url=url))
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
                    if next_url and urlsplit(next_url).netloc.lower() != urlsplit(effective_url).netloc.lower():
                        summary["pagination_stop"] = "cross_host"
                        LOG.warn("crawl.pagination_cross_host", source=source.id, next_url=next_url)
                        next_url = None
                    url = next_url
                    if url:
                        time.sleep(delay)
            else:
                raise RuntimeError(f"unsupported source kind: {source.kind}")

            # P20:配额封顶与关键词闸门必须排在正文补全**之前**(实测 2026-11-15 job 66:
            # feed 30 条 → 闸门扔掉 19 条,却先为这 30 条抓了 20 个文章页,33 秒网络全部
            # 花在"本次不入库"的条目上,还占满了补全预算,真正入库的 2 条反而没预算)。
            # 顺带修回执诚实性:闸门排在配额之后,``feed_total`` 才是订阅源真实条数,
            # ``items_extracted`` 仍是"进入入库流程的条数"(test_p12:612 的既有口径)。
            if limit:
                items = items[:limit]
            elif source.max_articles > 0:
                # P12批次3:RSS/HTML 全量 feed 可达数百条,未显式 limit 时按来源配置封顶
                summary["feed_total"] = len(items)
                items = items[: max(1, int(source.max_articles))]

            # P19 关键词闸门(入库侧):计划阶段决定去哪儿采,这里决定采回来的哪些算本轮要的。
            # 不命中的条目本次不入库,但必须计数 + 记日志,不得静默丢弃。
            # 代价诚实说明:闸门跑在补正文之前,只按**标题 + 订阅摘要**判定,
            # 正文里才出现的关键词这一轮抓不到;要精确命中就配 url_template 走检索式来源。
            keyword_filter = [str(k).strip() for k in (params.get("keyword_filter") or []) if str(k).strip()]
            if keyword_filter:
                kept: list[extract.ExtractedItem] = []
                hits: dict[str, int] = {}
                for item in items:
                    hit = extract.keyword_hit(item, keyword_filter)
                    if hit is None:
                        continue
                    kept.append(item)
                    hits[hit] = hits.get(hit, 0) + 1
                summary["keyword_filtered_out"] = len(items) - len(kept)
                summary["keyword_hits"] = hits
                if len(kept) < len(items):
                    LOG.info(
                        "crawl.keyword_filter",
                        source=source.id,
                        keywords=keyword_filter,
                        kept=len(kept),
                        filtered=summary["keyword_filtered_out"],
                        detail="闸门只看标题与订阅摘要(补正文之前);需要正文级命中请配 url_template 检索式来源",
                    )
                items = kept

            # P19:正文补全在浏览器会话**内**执行(render=browser 的来源复用同一个会话,
            # 不会每条新开一个浏览器 —— 实测 Edge 冷启动 2s+/条,任务会失控)
            if source.fetch_item_pages:
                _enrich_item_bodies(
                    items, source, effective_url, ua, rules_cache, delay, summary, browser_session
                )

        summary["items_extracted"] = len(items)
        # C-2:来源页/订阅地址不是文章身份(html 列表页把它的 URL 赋给每一条),
        # 必须显式告知去重判定,否则列表页正文会被按 URL 误判为重复。
        # C-3:cross_report 是写入侧跨库判定的归属回执(出参),用于 meta 与日志。
        cross_report: dict[str, object] = {}
        counts = store.insert_items(
            target_category,
            source.id,
            items,
            page_url=effective_url,
            cross_report=cross_report,
            page_urls=listing_pages,
        )
        summary.update(counts)
        if counts.get("url_identity_conflict"):
            # C-7b:同一 URL 位上是两篇不同文章 —— 不是"正常重复",必须写进 meta 与日志
            LOG.warn(
                "crawl.url_identity_conflict",
                source=source.id,
                category=target_category,
                conflicts=counts["url_identity_conflict"],
                detail="同一 URL 上出现了正文互不相同的文章;要修的是抽取器(逐条取自己的链接),不得删数据",
            )
        if counts.get("url_no_item_link"):
            LOG.warn(
                "crawl.no_item_link",
                source=source.id,
                category=target_category,
                count=counts["url_no_item_link"],
                detail="这些条目没拿到自己的链接,URL 退回列表页地址;整改:来源配 link_selector 或换列表页",
            )
        if cross_report.get("blocked") or cross_report.get("scan_errors"):
            # C-3:少掉的条目不是“正常重复”,要写明被谁持有;没有命中时不加键,
            # 保持 meta_json 结构不变(dashboard/metrics 不需要为跨库特判)。
            summary["cross_db"] = cross_report
            LOG.warn(
                "crawl.cross_db_duplicate",
                source=source.id,
                category=target_category,
                blocked=cross_report.get("blocked", 0),
                holders=sorted(cross_report.get("holders", {})),
                scan_errors=cross_report.get("scan_errors", []),
            )
        # C-4:采集与清洗必须是一条链。实测根因:恢复真实采集后 328 行里只有 43 行有
        # 清洗文本(13%),不是因为清洗跑不动(6 个命名空间 328 行实测 4.3 秒),而是
        # 采集入口从来没调用清洗管线,派生字段(cleaned_content/text_length/simhash/
        # cluster_id)长期落后于采集,下游取不到清洗文本还看不出为什么。
        # 管线失败绝不能吞掉已经落库的文章:单独记 pipeline_error,采集结论保持 ok。
        try:
            stats = pipeline.run_pipeline(target_category)
            summary["pipeline"] = {
                key: stats.get(key)
                for key in ("cleaned", "dates_normalized", "clusters", "near_duplicate_pairs", "clean_version", "indexed")
            }
        except Exception as exc:  # noqa: BLE001 - 派生字段落后不能反过来让采集判失败
            summary["pipeline_error"] = f"{type(exc).__name__}: {exc}"
            LOG.error(
                "crawl.pipeline_failed",
                source=source.id,
                category=target_category,
                error=summary["pipeline_error"],
            )
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
