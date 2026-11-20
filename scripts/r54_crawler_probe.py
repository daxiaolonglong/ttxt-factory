"""R54 爬虫底座最小通路探针(2026-11-20,docs/ARCH_R54.md §5-P2)。

目的:证明 "Scrapling 抓取 → 清洗 → 去重判断 → 入库" 在 Windows 纯本地环境真实跑通。
范围红线(与 ARCH_R54 影响面清单一致):
- 只抓 1 篇头条文章(toutiao_tech 频道页第一个 /article/ 链接),不做批量;
- 逐 URL 过 robots.gate 与 url_safety.check_url:不登录、不绕过访问控制、限速 2s;
- 绝不写 ``articles`` 表:入库目标是同类目库里的独立新表 ``r54_probe_articles``,
  回滚方式就一条 ``DROP TABLE r54_probe_articles``;
- 浏览器二进制与 profile、临时产物全部只落 D:\ttxt:
  PLAYWRIGHT_BROWSERS_PATH=02_crawler/.browser/ms-playwright(patchright chromium-1248),
  profile 与产物落 99_temp/r54_probe/(整目录可删,TTL 约定见 wiki);
- 不调用任何模型(本探针纯爬虫通路)。

用法: .venv\\Scripts\\python.exe scripts\\r54_crawler_probe.py
退出码 0 = 全通路 PASS;非 0 = 某阶段 FAIL(已打印阶段与原因)。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 必须在 import scrapling 之前钉住:浏览器二进制与 driver 临时产物都只落 D:\ttxt。
BROWSERS_DIR = ROOT / "02_crawler" / ".browser" / "ms-playwright"
TEMP_DIR = ROOT / "99_temp" / "pw_dl"
OUT_DIR = ROOT / "99_temp" / "r54_probe"
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSERS_DIR)
os.environ["TMP"] = str(TEMP_DIR)
os.environ["TEMP"] = str(TEMP_DIR)
TEMP_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(ROOT))

from ttxt import config as cfg  # noqa: E402
from ttxt.crawler import clean, robots, simhash, toutiao, url_safety  # noqa: E402
from ttxt.db import connect  # noqa: E402
from ttxt.crawler.store import articles_db_path  # noqa: E402

PROBE_RUN = datetime.now(timezone.utc).strftime("r54_%Y%m%d_%H%M%S")
CATEGORY = "technology"
CHANNEL_URL = "https://www.toutiao.com/ch/news_tech/"
RATE_LIMIT_S = 2.0
PROBE_TABLE = "r54_probe_articles"

summary: dict = {"probe_run": PROBE_RUN, "stages": {}, "artifacts": {}}


def _log(stage: str, **fields) -> None:
    summary["stages"][stage] = {k: str(v) for k, v in fields.items()}
    print(f"[{stage}] " + " ".join(f"{k}={v}" for k, v in fields.items()), flush=True)


def stage1_minimal_fetcher() -> None:
    """Scrapling 官方最小示例:静态 Fetcher(curl_cffi)+ CSS 提取。"""
    from scrapling.fetchers import Fetcher

    page = Fetcher.get("https://quotes.toscrape.com/", stealthy_headers=False)
    authors = page.css(".author::text").getall()
    quotes = page.css(".quote").getall()
    assert page.status == 200, f"status={page.status}"
    assert authors and quotes, f"authors={len(authors)} quotes={len(quotes)}"
    _log("1_minimal_fetcher", status=page.status, quotes=len(quotes), authors=len(set(authors)))


def _gate(url: str, ua: str, cache: dict) -> robots.RobotsRules:
    verdict = url_safety.check_url(url)
    if not verdict.allowed:
        raise RuntimeError(f"url_safety reject {url}: {verdict.reason}")
    rules, allowed = robots.gate(url, ua, cache)
    if not allowed:
        raise RuntimeError(f"robots disallow {url}: {rules.blocked_reason or 'unknown'}")
    return rules


def stage2_toutiao_one_article() -> dict:
    """真实抓 1 篇头条文章:频道页(浏览器渲染)→ 第一链接 → 文章页正文抽取。"""
    from scrapling.fetchers import StealthySession

    ua = str(cfg.load_config().get("crawler", {}).get("user_agent", "TouTiaoSmartFactory/0.1"))
    robot_cache: dict = {}
    _gate(CHANNEL_URL, ua, robot_cache)

    profile = OUT_DIR / "browser-profile"
    session = StealthySession(headless=True, max_pages=1, user_data_dir=str(profile))
    session.start()
    try:
        links: list = []
        for attempt in range(3):
            listing = session.fetch(CHANNEL_URL)
            links = toutiao.extract_listing(listing)
            if links:
                break
            time.sleep(3.0)  # 频道页是客户端渲染,偶发首帧未挂载:重取,不猜
        assert links, "频道页未提取到任何 /article/ 链接(重试 3 次后)"
        evidence = [{"url": u, "title": t} for u, t in links[:10]]
        (OUT_DIR / "listing_evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        article_url, listing_title = links[0]

        time.sleep(RATE_LIMIT_S)
        _gate(article_url, ua, robot_cache)
        page = session.fetch(article_url)
        assert page.status == 200, f"article status={page.status}"
        item = toutiao.extract_article(page, article_url)
    finally:
        session.close()

    assert len(item.content or "") >= 200, f"正文过短:{len(item.content or '')}"
    (OUT_DIR / "article_item.json").write_text(
        json.dumps(
            {
                "url": item.url,
                "title": item.title,
                "author": item.author,
                "published_at": item.published_at,
                "char_count": len(item.content),
                "excerpt": item.excerpt,
                "content_head": item.content[:800],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    summary["artifacts"]["listing_evidence"] = str(OUT_DIR / "listing_evidence.json")
    summary["artifacts"]["article_item"] = str(OUT_DIR / "article_item.json")
    _log(
        "2_toutiao_fetch",
        url=item.url,
        title=item.title[:40],
        listing_links=len(links),
        char_count=len(item.content),
        author=item.author,
        published_at=item.published_at,
    )
    return item


def stage3_dedupe_verdict(item) -> tuple[str, str]:
    """去重判断(只读比对既有 articles 表,绝不写入/改动它)。"""
    url_norm = clean.normalize_url(item.url)
    chat = clean.content_hash(item.title, item.content)
    sh = simhash.simhash64(item.content)

    db = articles_db_path(CATEGORY)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30.0)
    con.row_factory = sqlite3.Row
    try:
        url_hit = con.execute(
            "SELECT id FROM articles WHERE url=? OR url_normalized=? LIMIT 3", (item.url, url_norm)
        ).fetchall()
        hash_hit = con.execute("SELECT id FROM articles WHERE content_hash=? LIMIT 3", (chat,)).fetchall()
        min_dist, best_id = 64, None
        for row in con.execute("SELECT id, simhash FROM articles WHERE simhash IS NOT NULL"):
            dist = simhash.hamming(sh, simhash.from_hex(row["simhash"]))
            if dist < min_dist:
                min_dist, best_id = dist, row["id"]
    finally:
        con.close()

    detail = (
        f"url_normalized={url_norm} content_hash={chat} simhash={simhash.to_hex(sh)} "
        f"url_hits={[r['id'] for r in url_hit]} hash_hits={[r['id'] for r in hash_hit]} "
        f"min_hamming={min_dist} nearest_id={best_id}"
    )
    if url_hit or hash_hit:
        verdict = "duplicate"
    elif min_dist <= 3:
        verdict = "near_duplicate"
    else:
        verdict = "new"
    _log("3_dedupe", verdict=verdict, detail=detail)
    return verdict, detail


def stage4_write_probe_table(item, verdict: str, detail: str) -> None:
    """入库验证:只写独立新表 r54_probe_articles,既有 articles 一行都不碰。"""
    db = articles_db_path(CATEGORY)
    before = 0
    con = connect(db)
    try:
        con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {PROBE_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                probe_run TEXT NOT NULL,
                url TEXT NOT NULL,
                url_normalized TEXT NOT NULL,
                title TEXT,
                author TEXT,
                published_at TEXT,
                content_hash TEXT NOT NULL,
                simhash TEXT NOT NULL,
                char_count INTEGER NOT NULL,
                dedupe_verdict TEXT NOT NULL,
                dedupe_detail TEXT,
                fetched_at TEXT NOT NULL
            )
            """
        )
        before = con.execute(f"SELECT COUNT(*) FROM {PROBE_TABLE}").fetchone()[0]
        con.execute(
            f"""
            INSERT INTO {PROBE_TABLE}
                (probe_run, url, url_normalized, title, author, published_at,
                 content_hash, simhash, char_count, dedupe_verdict, dedupe_detail, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                PROBE_RUN,
                item.url,
                clean.normalize_url(item.url),
                item.title,
                item.author,
                item.published_at,
                clean.content_hash(item.title, item.content),
                simhash.to_hex(simhash.simhash64(item.content)),
                len(item.content),
                verdict,
                detail,
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        con.commit()
        after = con.execute(f"SELECT COUNT(*) FROM {PROBE_TABLE}").fetchone()[0]
        articles_untouched = con.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    finally:
        con.close()
    assert after == before + 1, f"probe table rows {before}->{after}"
    _log("4_write_probe_table", table=PROBE_TABLE, rows=f"{before}->{after}", articles_untouched=articles_untouched, db=str(db))


def main() -> int:
    try:
        stage1_minimal_fetcher()
        item = stage2_toutiao_one_article()
        verdict, detail = stage3_dedupe_verdict(item)
        stage4_write_probe_table(item, verdict, detail)
    except Exception as exc:  # noqa: BLE001
        summary["result"] = f"FAIL: {type(exc).__name__}: {exc}"
        (OUT_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(summary["result"], file=sys.stderr, flush=True)
        return 1
    summary["result"] = "PASS"
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"PASS probe_run={PROBE_RUN} summary={OUT_DIR / 'summary.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
