# -*- coding: utf-8 -*-
"""P30:逐篇来源身份清单生成器(联网,只读库)。

对库里中国新闻网两频道在库文章**逐篇回到文章页**读页头署名「来源:xxx」,
配合库里正文电头「中新网X月X日电 / 新华社X电」判定身份,落盘
`03_intelligence/source_identity/chinanews-<日期>.jsonl`。

纪律:
- 逐 URL 先过 robots.txt,拒绝就跳过并如实记 unknown,不得绕过;
- 按来源限速(默认 1.6 秒),不登录、不绕过访问控制;
- 只读连库(`mode=ro`),只写清单文件,不写生产库;
- 页面抓不到 = unknown,并写失败原因;**不许按域名假定来源**。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from ttxt.analysis import source_identity as si
from ttxt.crawler import fetcher, robots

ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "Mozilla/5.0 (compatible; ttxt-factory/1.0; +https://github.com/daxiaolonglong/ttxt-factory)"
CHINANEWS_SOURCES = ("chinanews_society", "chinanews_culture")


def load_rows(urls: set[str] | None = None) -> list[dict]:
    rows: list[dict] = []
    cats = ("demo", "finance", "health", "history", "social", "technology") if urls else ("history", "social")
    for cat in cats:
        db = ROOT / "01_categories" / cat / "articles.db"
        if not db.exists():
            continue
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            if urls:
                placeholders = ",".join("?" * len(urls))
                sql = (f"SELECT id, category, source, url, title, cleaned_content, content "
                       f"FROM articles WHERE url IN ({placeholders}) ORDER BY id")
                cur = con.execute(sql, tuple(urls))
            else:
                cur = con.execute(
                    "SELECT id, category, source, url, title, cleaned_content, content "
                    "FROM articles WHERE source IN (?, ?) ORDER BY id", CHINANEWS_SOURCES)
            for r in cur:
                rows.append(dict(r))
        finally:
            con.close()
    return rows


def page_text(page: object) -> str:
    getter = getattr(page, "get_all_text", None)
    if callable(getter):
        try:
            return str(getter() or "")
        except Exception:  # noqa: BLE001 - 取不到全量文本就退回选择器
            pass
    return str(getattr(page, "text", "") or "")


def main() -> int:
    parser = argparse.ArgumentParser(description="逐篇取证中国新闻网在库文章的来源身份")
    parser.add_argument("--out", default="03_intelligence/source_identity/chinanews-20261116.jsonl")
    parser.add_argument("--rate-s", type=float, default=1.6)
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 篇(调试用)")
    parser.add_argument("--show", action="store_true", help="把汇总同时打到控制台")
    parser.add_argument("--urls-file", default="", help="只复核该文件里的 URL(每行一条,跨类目查库)")
    parser.add_argument("--render", action="store_true",
                        help="用本机浏览器渲染抓取(有些站点对静态抓取返回跳转到本机地址的防机器人响应)")
    args = parser.parse_args()

    urls: set[str] | None = None
    if args.urls_file:
        urls_path = ROOT / args.urls_file
        urls = {line.strip() for line in urls_path.read_text(encoding="utf-8").splitlines() if line.strip()}
    rows = load_rows(urls)
    if args.limit:
        rows = rows[:args.limit]
    print(f"库里中国新闻网在库文章 {len(rows)} 篇,逐篇回页面取证(限速 {args.rate_s} 秒)")

    rules_cache: dict[str, robots.RobotsRules] = {}
    out_rows: list[dict] = []
    now_iso = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).replace(microsecond=0).isoformat()
    for index, art in enumerate(rows):
        url = art["url"] or ""
        parsed = urlparse(url)
        marker: str | None = None
        probe_note = ""
        page_head = ""
        if index:
            time.sleep(args.rate_s)
        try:
            rules = rules_cache.get(parsed.netloc)
            if rules is None:
                rules = robots.fetch_rules(url, USER_AGENT)
                rules_cache[parsed.netloc] = rules
            if not rules.allowed(parsed.path):
                probe_note = f"robots.txt 拒绝 {parsed.path},未抓取(不得绕过)"
            else:
                result = fetcher.fetch_page(url, session=None, render=args.render)
                text = page_text(result.page)
                page_head = text[:2000]
                marker = si.extract_page_marker(text)
                if marker is None:
                    # 有些站点(如第一财经)不写「来源:xxx」,发布机构放在标题下方的署名行
                    # `<div class="title"><p><span class="f-fl">新华社</span>...`。
                    # 只在**能归一到已知机构**时才采用,取到人名/图注一律丢弃(判 unknown 更保守)。
                    known = set(si.AGENCY_CANON.values())
                    for selector in ("span.source", ".source", "div.title p span.f-fl"):
                        found = list(result.page.css(selector))
                        alt = str(getattr(found[0], "text", "") or "").strip() if found else ""
                        if alt and si.canon_agency(alt) in known:
                            marker = alt
                            break
                probe_note = f"HTTP {result.status};页头署名={marker or '无'}"
        except Exception as exc:  # noqa: BLE001 - 单篇失败不许中断整轮取证
            probe_note = f"抓取失败 {type(exc).__name__}: {str(exc)[:120]}"

        dateline = si.extract_dateline(art.get("cleaned_content") or art.get("content"))
        # 库里正文可能没补全(列表页摘要不含署名),这时用文章页前 2000 字的电头兜底;
        # 只在库里查不到时才用,避免页面导航/页脚字样覆盖正文证据。
        if dateline is None and page_head:
            dateline = si.extract_dateline(page_head)
        own_agency = si.SOURCE_PUBLISHER.get(art["source"]) or "chinanews"
        identity = si.classify(marker, dateline, own_agency=own_agency)
        if identity.kind == si.KIND_UNKNOWN and probe_note:
            identity = si.Identity(identity.kind, None, marker, dateline, f"{identity.evidence};{probe_note}")
        row = si.inventory_row(category=art["category"], article_id=int(art["id"]), url=url,
                               title=art.get("title"), marker=marker, dateline=dateline,
                               identity=identity, probed_at=now_iso)
        row["source"] = art["source"]
        row["own_agency"] = own_agency
        row["probe_note"] = probe_note
        out_rows.append(row)
        print(f"  [{index + 1}/{len(rows)}] {row['kind']:<18} agency={row['agency'] or '-':<14} "
              f"页头={marker or '-':<12} 电头={dateline or '-':<8} {(art.get('title') or '')[:26]}")

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(
        json.dumps(r, ensure_ascii=False) + "\n" for r in out_rows), encoding="utf-8")

    kinds = Counter(r["kind"] for r in out_rows)
    agencies = Counter(r["agency"] or "(unknown)" for r in out_rows)
    reprint_agencies = Counter(r["agency"] for r in out_rows if r["kind"] == si.KIND_REPRINT)
    summary = {
        "probed": len(out_rows),
        "kinds": dict(kinds),
        "agencies": dict(agencies),
        "reprint_agencies": dict(reprint_agencies),
        "independent_eligible": sum(1 for r in out_rows if r["independent_eligible"]),
        "first_hand_of_event": sum(1 for r in out_rows if r["is_first_hand_of_event"]),
        "probed_at": now_iso,
        "out": str(out.relative_to(ROOT)),
    }
    report = json.dumps(summary, ensure_ascii=False, indent=2)
    summary_path = out.parent / f"{out.stem}.summary.json"
    summary_path.write_text(report + "\n", encoding="utf-8")
    print(f"汇总写入 {summary_path.relative_to(ROOT)}")
    if args.show:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
