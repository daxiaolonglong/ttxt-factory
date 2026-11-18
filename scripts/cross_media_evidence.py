# -*- coding: utf-8 -*-
"""P30:血统可判的跨媒体同题匹配(只读)。

把逐篇来源身份清单接进配对器:候选对必须满足
①不同域名 ②不同机构 ③双方都不是聚合平台/身份不明 ④标题相似度达标。
任何一条不满足都要落到"被拒"清单并写明理由,不许静默丢弃。
输出 `03_intelligence/source_identity/cross_media-<日期>.json`。
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from ttxt.analysis import source_identity as si

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = ("demo", "finance", "health", "history", "social", "technology")


def load_identities(inventory: Path) -> dict[str, si.Identity]:
    ids: dict[str, si.Identity] = {}
    if not inventory.exists():
        return ids
    for line in inventory.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        ids[row["url"]] = si.Identity(row["kind"], si.canon_agency(row.get("agency")),
                                      row.get("page_marker"),
                                      row.get("dateline_agency"), row.get("evidence") or "")
    return ids


def load_items(cat: str) -> list[dict]:
    import sqlite3
    db = ROOT / "01_categories" / cat / "articles.db"
    if not db.exists():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = []
    try:
        for r in con.execute("SELECT id, source, url, title FROM articles ORDER BY id"):
            rows.append({"url": r["url"], "title": r["title"], "source": r["source"],
                         "article_id": r["id"], "domain": si.domain_of(r["url"])})
    finally:
        con.close()
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="跨媒体同题配对(带来源血统判定)")
    parser.add_argument("--inventory", default="03_intelligence/source_identity/chinanews-20261116.jsonl")
    parser.add_argument("--out", default="03_intelligence/source_identity/cross_media-20261116.json")
    args = parser.parse_args()

    inv_path = ROOT / args.inventory
    probed = load_identities(inv_path)
    print(f"逐篇身份清单 {len(probed)} 条:{inv_path.relative_to(ROOT)}")
    report = {"inventory": str(inv_path.relative_to(ROOT)), "categories": {}, "totals": {}}
    total_pairs = total_rejected = 0
    reason_counter: Counter[str] = Counter()
    for cat in CATEGORIES:
        items = load_items(cat)
        ids: dict[str, si.Identity] = {}
        for it in items:
            ids[it["url"]] = probed.get(it["url"]) or si.identity_for_source(it["source"])
        pairs, rejected = si.find_pairs(items, ids)
        kinds = Counter(i.kind for i in ids.values())
        agencies = Counter((i.agency or "-") for i in ids.values())
        report["categories"][cat] = {
            "articles": len(items),
            "identity_kinds": dict(kinds),
            "agencies": dict(agencies),
            "pairs": [{
                "left": {"url": p["left"]["url"], "title": p["left"]["title"], "agency": p["agencies"][0]},
                "right": {"url": p["right"]["url"], "title": p["right"]["title"], "agency": p["agencies"][1]},
                "score": p["score"],
            } for p in pairs],
            "rejected_sample": rejected[:12],
            "rejected_reasons": dict(Counter(r["reason"].split(":")[0].split(",")[0] for r in rejected)),
        }
        total_pairs += len(pairs)
        total_rejected += len(rejected)
        for r in rejected:
            reason_counter[r["reason"].split(":")[0].split(",")[0]] += 1
        print(f"[{cat}] 文章 {len(items)} 家来源 {len(agencies)} 家;"
              f"成对 {len(pairs)} 组;被拒 {len(rejected)} 组")
    report["totals"] = {"pairs": total_pairs, "rejected": total_rejected,
                        "reject_reasons": dict(reason_counter)}
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"写出 {out.relative_to(ROOT)};总计 成对 {total_pairs} / 被拒 {total_rejected}")
    for reason, n in reason_counter.most_common():
        print(f"   拒因:{reason} × {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
