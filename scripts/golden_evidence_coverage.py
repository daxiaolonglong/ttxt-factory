# -*- coding: utf-8 -*-
"""P29 取证(只读):黄金集的"独立证据"到底有多少,以及还差多少才够 50% 门禁。

不看生产数据不改,只回答三个问题:
1. 现有 61 张卡里,A/B 级各有多少(门禁要 A/B 级证据 ≥50%);
2. 语料里有多少"同一件事、两家以上互不相关的域名都报道过"的现成材料
   —— 这才是 B 级证据的正解,不是把同一个 WHO 频道再刷一遍;
3. A 级来源现在只有 who_cn_news 一个,它到底还能撑几张卡。

用法(用项目 venv,只读连库,跑完请执行 scripts/p28_wal_sidecar_cleanup.py 收边车):
    python -X utf8 scripts/golden_evidence_coverage.py
禁止:跑测试的同时连生产库(规则 48)。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
CARDS = ROOT / "10_tests" / "golden" / "golden_set_v2.jsonl"
CATS_DIR = ROOT / "01_categories"
AB = ("A", "B")


def _rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _domain(url: str) -> str:
    host = (urlparse(url or "").netloc or "").lower()
    for prefix in ("www.", "m.", "news.", "finance.", "tech."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host or "?"


def card_census() -> dict:
    cards = _rows(CARDS)
    tiers = Counter(c.get("source_tier") for c in cards)
    urls = {c.get("source_url") for c in cards}
    domains = Counter(_domain(c.get("source_url")) for c in cards)
    corrob = sum(1 for c in cards if c.get("corroborating_urls"))
    return {
        "cards": len(cards),
        "by_tier": dict(tiers),
        "ab_cards": sum(tiers.get(t, 0) for t in AB),
        "ab_ratio": round(sum(tiers.get(t, 0) for t in AB) / max(1, len(cards)), 4),
        "distinct_source_urls": len(urls),
        "distinct_domains": len(domains),
        "domain_counts": dict(domains),
        "cards_with_corroborating_urls": corrob,
        "need_ab_for_50pct": -(-len(cards) // 2) - sum(tiers.get(t, 0) for t in AB),
    }


def corpus_census() -> dict:
    out: dict = {"categories": {}, "total_articles": 0}
    all_rows: list[dict] = []
    for db in sorted(CATS_DIR.glob("*/articles.db")):
        cat = db.parent.name
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                """
                SELECT id, source, url, title,
                       coalesce(nullif(cleaned_content,''), content) AS text,
                       cluster_id, credibility, text_length
                FROM articles
                """
            ).fetchall()
        finally:
            con.close()
        items = [dict(r) for r in rows]
        for it in items:
            it["category"] = cat
            it["domain"] = _domain(it["url"])
        all_rows.extend(items)
        by_source = Counter(it["source"] for it in items)
        by_domain = Counter(it["domain"] for it in items)
        usable = [it for it in items if 60 <= (it["text_length"] or 0) <= 20000]
        out["categories"][cat] = {
            "articles": len(items),
            "usable_60_20000_chars": len(usable),
            "sources": dict(by_source),
            "domains": dict(by_domain),
        }
        out["total_articles"] += len(items)

    # B 级证据的现成材料:同一个 cluster_id 聚类里出现两家以上互不相关的域名
    by_cluster: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for it in all_rows:
        if it["cluster_id"]:
            by_cluster[(it["category"], it["cluster_id"])].append(it)
    cross_domain = []
    for (cat, cluster), group in by_cluster.items():
        domains = {it["domain"] for it in group}
        if len(domains) >= 2 and len(group) >= 2:
            cross_domain.append({
                "category": cat, "cluster": cluster[:12], "articles": len(group),
                "domains": sorted(domains),
                "titles": [it["title"][:38] for it in group[:4]],
            })
    cross_domain.sort(key=lambda d: (-d["articles"], d["category"]))
    out["cross_domain_clusters"] = {
        "count": len(cross_domain),
        "by_category": dict(Counter(d["category"] for d in cross_domain)),
        "with_3plus_domains": sum(1 for d in cross_domain if len(d["domains"]) >= 3),
        "examples": cross_domain[:8],
    }
    out["articles_by_domain"] = dict(Counter(it["domain"] for it in all_rows))
    return out


def main() -> int:
    report = {
        "golden_set_v2": card_census(),
        "corpus": corpus_census(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True))
    g = report["golden_set_v2"]
    print(f"\n门禁要 A/B 级证据 ≥50%:现在 {g['ab_cards']}/{g['cards']} = "
          f"{round(g['ab_ratio'] * 100, 1)}%,还差 {g['need_ab_for_50pct']} 张 A/B 卡")
    print(f"61 张卡只覆盖 {g['distinct_source_urls']} 个网址 / {g['distinct_domains']} 个域名;"
          f"带独立第二来源的卡 {g['cards_with_corroborating_urls']} 张")
    print(f"语料 {report['corpus']['total_articles']} 篇里,跨域名同题聚类 "
          f"{report['corpus']['cross_domain_clusters']['count']} 组"
          f"(3 家以上域名 {report['corpus']['cross_domain_clusters']['with_3plus_domains']} 组)"
          " —— 这是做 B 级卡的现成材料")
    return 0


if __name__ == "__main__":
    sys.exit(main())
