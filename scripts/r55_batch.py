"""R55(2026-11-20):关键词驱动的小批量采集 —— 正式链路驱动脚本。

它**不做采集、不做去重、不写库**:这些全部走生产模块
(``keyword_crawl.plan_category/enqueue_plan`` → ``crawl_jobs`` 台账 →
``runner.run_next`` → ``toutiao.collect`` → ``store.insert_items`` 三道闸)。
本脚本只负责三件事:

1. **跑前只读预检**(用户放行条件,风险 B 第二选项):
   - 既有 ``articles`` 行指纹快照(``id/title_len/content_len/content_hash/simhash/published_at/fetched_at``);
   - 当日已采 URL 名单(按 ``articles.fetched_at`` 当日,只读)+ 全库 URL 存在性名单;
   - 类目库一致性备份(``sqlite3.backup``,只写 99_temp/,可整目录删)。
2. **跑后逐行 diff**:既有行任何字段变化(含 ``bodies_upgraded``/``published_at`` 回填)
   一律如实列出并判 ``STOP_AND_REPORT``,本脚本不自动解释成"可接受"。
3. **逐篇清单**:频道页链接池 + 关键词命中 + 入库/拒因,写 ``99_temp/r55_batch/``。

关于 ``--listing-url-override``:它只是 ``runner`` 既有的计划参数(仅允许同 host,
robots 在计划期复核)。默认不换页 —— 当日已采 URL 名单是**预检对账名单**,用来逐条回答
"为什么这批里没有它 / 为什么这条被拒",不是用来绕闸门的;闸门的重复判定照常生效。
实测:头条只有 ``news_tech/news_finance/news_history/news_regimen/news_military/news_game``
这类频道 slug,``news_science`` 等是 404,``news_car`` 跳站外(跨 host 会被 runner 拒)。

用法:
    .venv\\Scripts\\python.exe scripts\\r55_batch.py                 # 预检 + 出计划,不入队不执行
    .venv\\Scripts\\python.exe scripts\\r55_batch.py --apply         # 预检 + 入队 + 执行 + 逐行 diff
退出码 0 = 通路闭合且既有行零变化;2 = 既有行出现变化(必须停手上报);3 = 采集通路失败。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ttxt import config as cfg  # noqa: E402
from ttxt.crawler import clean, extract, keyword_crawl, runner, store  # noqa: E402
from ttxt.crawler.store import articles_db_path  # noqa: E402
from ttxt.db import connect  # noqa: E402

OUT_DIR = ROOT / "99_temp" / "r55_batch"
FINGERPRINT_COLUMNS = (
    "id", "title", "content", "excerpt", "url", "url_normalized", "content_hash",
    "simhash", "published_at", "fetched_at", "cleaned_content", "text_length",
    "cluster_id", "clean_version", "processor_version", "completeness",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _dump(name: str, payload: object) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[out] {path}", flush=True)
    return path


def open_ro(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30.0)
    con.row_factory = sqlite3.Row
    return con


def fingerprint(db_path: Path) -> dict[int, dict[str, object]]:
    con = open_ro(db_path)
    try:
        rows = con.execute(
            "SELECT id, title, content, excerpt, url, url_normalized, content_hash, simhash,"
            " published_at, fetched_at, cleaned_content, text_length, cluster_id, clean_version,"
            " processor_version, completeness FROM articles"
        ).fetchall()
    finally:
        con.close()
    out: dict[int, dict[str, object]] = {}
    for row in rows:
        record: dict[str, object] = {}
        for column in FINGERPRINT_COLUMNS:
            value = row[column]
            if column in ("title", "content", "excerpt", "cleaned_content") and value is not None:
                record[f"{column}_len"] = len(str(value))
            else:
                record[column] = value
        out[int(row["id"])] = record
    return out


def url_inventory(db_path: Path) -> dict[str, list[str]]:
    """当日已采 URL 名单 + 全库 URL 名单(只读,预检用)。"""
    con = open_ro(db_path)
    try:
        today = [
            dict(row)
            for row in con.execute(
                "SELECT id, url, url_normalized, title, source, fetched_at FROM articles"
                " WHERE substr(fetched_at,1,10) = date('now') ORDER BY fetched_at, id"
            )
        ]
        all_urls = [
            str(row["url_normalized"])
            for row in con.execute("SELECT url_normalized FROM articles ORDER BY id")
        ]
    finally:
        con.close()
    return {"today_rows": today, "all_url_normalized": all_urls}


def backup_db(db_path: Path, dest_dir: Path) -> dict[str, str]:
    """在线一致性备份(sqlite3 backup API):只写 99_temp/,整目录可删。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "technology_articles_backup.db"
    src = connect(db_path)
    try:
        target = connect(dest)
        try:
            src.backup(target)
        finally:
            target.close()
    finally:
        src.close()
    return {"path": str(dest), "bytes": str(dest.stat().st_size)}


def pending_jobs() -> list[dict[str, object]]:
    con = open_ro(store.system_db_path())
    try:
        return [
            {"id": int(r["id"]), "source": str(r["source"]), "status": str(r["status"])}
            for r in con.execute(
                "SELECT id, source, status FROM crawl_jobs"
                " WHERE status IN ('queued','running','failed') ORDER BY id"
            )
        ]
    finally:
        con.close()


def plan_for(category: str, source_ids: list[str], keyword_limit: int, override: str | None) -> dict:
    plan = keyword_crawl.plan_category(category, source_ids=source_ids, keyword_limit=keyword_limit)
    if override:
        for entry in plan.get("plans", []):
            if entry.get("source") in source_ids and not entry.get("url_override"):
                entry["url_override"] = override
                entry["note"] = (
                    "R55 预检:默认频道页当日已被调度器采过,按放行要求换同 host 频道页,"
                    "避免当日已采 URL 进入本批(仅 url_override 计划参数,robots 已在计划期复核)"
                )
    return plan


def link_pool(category: str, source_ids: list[str], override: str | None, keywords: list[str]) -> list[dict[str, object]]:
    """只读渲染一次频道页,取链接池 + 关键词命中判定(取证用,不抓正文、不入库)。

    命中判定只跑在**标题**上(``content=""``),比生产闸门(标题+正文)更保守:
    预检里的 ``keyword_hit=null`` 不等于本轮一定被卡,以回执 ``keyword_hits`` 为准。
    """
    from ttxt.crawler import fetcher, robots, sources as source_loader, toutiao

    source = next(s for s in source_loader.load_sources() if s.id == source_ids[0])
    url = override or source.url
    ua = str(cfg.load_config().get("crawler", {}).get("user_agent", "TouTiaoSmartFactory/0.1"))
    rules, allowed = robots.gate(url, ua, {})
    if rules.unknown or not allowed:
        raise RuntimeError(f"链接池取证被拦:{url} robots_unknown={rules.unknown} allowed={allowed}")
    with fetcher.browser_session() as session:
        page = fetcher.fetch_page(url, render=True, session=session)
    links = toutiao.extract_listing(page.page)
    rows: list[dict[str, object]] = []
    for link, title in links:
        item = extract.ExtractedItem(title=title, content="", excerpt="", author=None, published_at=None, url=link)
        rows.append(
            {
                "url": link,
                "url_normalized": clean.normalize_url(link),
                "listing_title": title,
                "keyword_hit_title_only": extract.keyword_hit(item, keywords),
            }
        )
    return rows


def reconcile(before: dict[int, dict[str, object]], category: str, window_start: str, pool: list[dict[str, object]], receipts: list[dict]) -> dict:
    """跑后逐行 diff。``before`` 必须是**跑前**的指纹且键为 int(从 JSON 复原时要转回 int)。"""
    db_path = articles_db_path(category)
    before = {int(key): value for key, value in before.items()}
    after = fingerprint(db_path)
    changed = []
    for article_id, old in before.items():
        new = after.get(article_id)
        if new is None:
            changed.append({"id": article_id, "change": "row_deleted"})
            continue
        fields = [key for key in old if old[key] != new[key]]
        if fields:
            changed.append(
                {
                    "id": article_id,
                    "fields": fields,
                    "before": {key: old[key] for key in fields},
                    "after": {key: new[key] for key in fields},
                    "url": old.get("url"),
                    "title_head": str(old.get("title") or "")[:60],
                }
            )
    new_ids = sorted(set(after) - set(before))
    new_rows: list[dict[str, object]] = []
    con = open_ro(db_path)
    try:
        for article_id in new_ids:
            got = con.execute(
                "SELECT title, url, url_normalized, source, published_at, fetched_at,"
                " length(content) AS content_len, completeness FROM articles WHERE id = ?",
                (article_id,),
            ).fetchone()
            if got is None:
                continue
            new_rows.append(
                {
                    "id": article_id,
                    "title": str(got["title"] or "")[:120],
                    "url": got["url"],
                    "url_normalized": got["url_normalized"],
                    "source": got["source"],
                    "content_len": got["content_len"],
                    "completeness": got["completeness"],
                    "published_at": got["published_at"],
                    "fetched_at": got["fetched_at"],
                }
            )
    finally:
        con.close()
    return {
        "before_rows": len(before),
        "after_rows": len(after),
        "existing_rows_changed": changed,
        "new_rows": new_rows,
        "window_start": window_start,
    }


def per_item_list(pool: list[dict[str, object]], inventory: dict[str, list[str]], receipts: list[dict], new_rows: list[dict]) -> list[dict[str, object]]:
    """逐篇清单:标题、来源、是否有正文、去重结论、入库 id 或拒因。"""
    today_urls = {str(row["url_normalized"]) for row in inventory["today_rows"]}
    all_urls = set(inventory["all_url_normalized"])
    inserted_by_url = {str(row["url_normalized"]): row for row in new_rows}
    receipt = receipts[-1] if receipts else {}
    meta = receipt.get("meta") or {}
    filtered_out = int(meta.get("keyword_filtered_out") or 0)
    article_errors = int(meta.get("article_errors") or 0)
    # R56:拒收留痕现在逐条进 meta_json.rejected(url/title/reason/basis),
    # 被关键词闸/长度闸挡掉的条目在这里能逐 URL 对上 —— R55 的缺陷②(聚合计数无从归属)关闭。
    rejected_by_url = {
        clean.normalize_url(str(rec.get("url") or "")): rec
        for rec in (meta.get("rejected") or [])
    }
    # 回执只有聚合的 keyword_hits(词→命中位置明细),没有逐 URL 归属;命中词按标题级预检给出,
    # 正文级命中在逐篇清单里只能由"入库/被卡"的计数反推,故 reason 里注明推断依据。
    actual_hits: dict[str, str] = {}
    rows: list[dict[str, object]] = []
    for index, entry in enumerate(pool[:10]):
        url_normalized = str(entry["url_normalized"])
        hit = entry["keyword_hit_title_only"] or actual_hits.get(url_normalized)
        keyword_name = hit["keyword"] if isinstance(hit, dict) else hit
        inserted = inserted_by_url.get(url_normalized)
        trace = rejected_by_url.get(url_normalized)
        if inserted is not None:
            disposition = "inserted"
            reason = (
                f"关键词命中「{keyword_name}」→ 新行入库"
                if keyword_name
                else "关键词命中在正文(回执 keyword_hits 聚合),标题级未命中 → 新行入库"
            )
            article_id = inserted["id"]
            content_len = inserted["content_len"]
        elif trace is not None:
            disposition = "rejected"
            basis = trace.get("basis") or {}
            if trace.get("reason") == "keyword_no_hit":
                reason = (
                    f"keyword_no_hit(标题+正文未命中本轮关键词;闸门计数={filtered_out},"
                    f"判定范围={basis.get('scope')},正文长={basis.get('body_len')})"
                )
            else:
                reason = (
                    f"body_below_intake_line(正文 {basis.get('body_len')} 字 < 入库拒收线 "
                    f"{basis.get('threshold')} 字)"
                )
            article_id = None
            content_len = basis.get("body_len")
        elif url_normalized in today_urls:
            disposition = "rejected"
            reason = "duplicate_url:当日已采 URL(预检名单命中,闸门拒绝,未改动既有行)"
            article_id = None
            content_len = None
        elif url_normalized in all_urls:
            disposition = "rejected"
            reason = "duplicate_url:库内既有 URL(闸门拒绝,未改动既有行)"
            article_id = None
            content_len = None
        elif hit is None:
            disposition = "rejected"
            reason = f"keyword_filtered_out(标题/正文未命中本轮关键词;闸门计数={filtered_out},回执 keyword_hits={meta.get('keyword_hits')})"
            article_id = None
            content_len = None
        else:
            disposition = "fetch_failed"
            reason = f"文章页抓取失败(article_errors={article_errors}),本次未入库"
            article_id = None
            content_len = None
        rows.append(
            {
                "seq": index + 1,
                "listing_title": entry["listing_title"],
                "url": entry["url"],
                "source": "toutiao_tech",
                "keyword_hit": hit,
                "in_today_urls": url_normalized in today_urls,
                "in_library_before": url_normalized in all_urls,
                "disposition": disposition,
                "reason": reason,
                "article_id": article_id,
                "content_len": content_len,
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="R55 关键词驱动小批量(正式链路驱动)")
    parser.add_argument("--category", default="technology")
    parser.add_argument("--source", action="append", default=None, help="限定来源 id(默认 toutiao_tech)")
    parser.add_argument("--keyword-limit", type=int, default=6)
    parser.add_argument("--listing-url-override", default=None)
    parser.add_argument("--no-override", action="store_true", help="不换页,用来源默认频道页")
    parser.add_argument("--max-jobs", type=int, default=1)
    parser.add_argument("--apply", action="store_true", help="入队并执行(不加则只出计划)")
    args = parser.parse_args()

    category = args.category
    source_ids = args.source or ["toutiao_tech"]
    override = None if args.no_override else args.listing_url_override
    db_path = articles_db_path(category)

    started_at = _utc_now()
    print(f"[r55] category={category} sources={source_ids} override={override} apply={args.apply}", flush=True)

    pre_baseline = fingerprint(db_path)
    pre_inventory = url_inventory(db_path)
    backup = backup_db(db_path, OUT_DIR)
    pending = pending_jobs()
    print(
        f"[pre] rows={len(pre_baseline)} today_urls={len(pre_inventory['today_rows'])} "
        f"library_urls={len(pre_inventory['all_url_normalized'])} backup={backup} pending_jobs={pending}",
        flush=True,
    )
    _dump("precheck_baseline.json", {"generated_at": started_at, "rows": len(pre_baseline), "fingerprints": pre_baseline})
    _dump("precheck_today_urls.json", pre_inventory)
    _dump("precheck_pending_jobs.json", pending)
    if pending:
        print("[abort] 队列里有别人的任务,先不动:避免误跑默认频道批次", flush=True)
        return 3

    plan = plan_for(category, source_ids, args.keyword_limit, override)
    _dump("plan.json", plan)
    print(f"[plan] keywords={plan['keywords']} plans={[p.get('reason') for p in plan['plans']]} rejected={plan['rejected']}", flush=True)

    entry_keywords: list[str] = []
    for entry in plan.get("plans", []):
        entry_keywords.extend([str(k) for k in (entry.get("keyword_filter") or entry.get("keywords") or [])])

    if not args.apply:
        print("[dry] 未入队(缺 --apply),链接池取证也留给 --apply 那次渲染,避免多余页请求", flush=True)
        return 0

    # 链接池取证紧接入队之前渲染:头条频道页是动态流,两次渲染间隔越长,
    # "预检池"与"批次实际用的池"错位越大 —— 逐篇清单的归属可信度取决于这个间隔。
    pool = link_pool(category, source_ids, override, entry_keywords)
    today_set = {str(row["url_normalized"]) for row in pre_inventory["today_rows"]}
    all_set = set(pre_inventory["all_url_normalized"])
    collision = {
        "listing_url": override or "source default",
        "first_10_collide_today": [
            str(entry["url_normalized"]) for entry in pool[:10] if str(entry["url_normalized"]) in today_set
        ],
        "first_10_collide_library": [
            str(entry["url_normalized"]) for entry in pool[:10] if str(entry["url_normalized"]) in all_set
        ],
        "note": "碰撞只说明闸门的重复判定会生效;本脚本不靠换页规避闸门,预检名单用于逐条对账",
    }
    _dump("listing_pool.json", {"pool": pool, "collision_with_precheck": collision})
    print(
        f"[pool] links={len(pool)} title_level_hits={sum(1 for p in pool if p['keyword_hit_title_only'])} "
        f"collide_today={len(collision['first_10_collide_today'])} collide_library={len(collision['first_10_collide_library'])}",
        flush=True,
    )

    keyword_crawl.enqueue_plan(plan)
    _dump("plan_enqueued.json", plan)
    print(f"[enqueue] queued={plan.get('queued_jobs')} reused={plan.get('reused_jobs')}", flush=True)

    receipts: list[dict] = []
    for _ in range(max(1, args.max_jobs)):
        summary = runner.run_next(owner="r55-batch", category=category)
        if summary is None:
            break
        receipts.append(dict(summary))
        con = open_ro(store.system_db_path())
        try:
            row = con.execute("SELECT meta_json FROM crawl_jobs WHERE id = ?", (int(summary["job_id"]),)).fetchone()
        finally:
            con.close()
        receipts[-1]["meta"] = json.loads(row["meta_json"]) if row and row["meta_json"] else {}
        print(
            f"[job] {receipts[-1]['job_id']} ok={receipts[-1]['ok']} inserted={receipts[-1].get('inserted')} "
            f"duplicates={receipts[-1].get('duplicates')} keyword_filtered_out={receipts[-1].get('keyword_filtered_out')} "
            f"body_rejected={receipts[-1].get('body_rejected')} rejected_trace={len((receipts[-1].get('meta') or {}).get('rejected') or [])} "
            f"bodies_upgraded={receipts[-1].get('bodies_upgraded')} error={receipts[-1].get('error')}",
            flush=True,
        )
    _dump("job_receipts.json", receipts)

    diff = reconcile(pre_baseline, category, started_at, pool, receipts)
    items = per_item_list(pool, pre_inventory, receipts, diff["new_rows"])
    _dump("diff_existing_rows.json", diff)
    _dump("per_item_list.json", items)

    receipt_counts = {
        key: sum(int((r.get("meta") or {}).get(key) or 0) for r in receipts)
        for key in ("inserted", "duplicates", "keyword_filtered_out", "body_rejected", "bodies_upgraded", "bodies_kept_existing", "url_identity_conflict", "url_no_item_link", "article_errors", "pages_fetched")
    }
    _dump("summary.json", {
        "started_at": started_at,
        "finished_at": _utc_now(),
        "category": category,
        "sources": source_ids,
        "listing_url_override": override,
        "keywords": plan["keywords"],
        "jobs": [r.get("job_id") for r in receipts],
        "job_counts": receipt_counts,
        "diff_rows": {"before": diff["before_rows"], "after": diff["after_rows"], "new": len(diff["new_rows"]), "changed_existing": len(diff["existing_rows_changed"])},
        "per_item": items,
    })
    print(
        f"[diff] before={diff['before_rows']} after={diff['after_rows']} new={len(diff['new_rows'])} "
        f"changed_existing={len(diff['existing_rows_changed'])} counts={receipt_counts}",
        flush=True,
    )
    if diff["existing_rows_changed"]:
        print("[STOP] 既有行出现字段变化 —— 停手上报,不自证可接受", flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
