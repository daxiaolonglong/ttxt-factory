"""R40 T-10 证据采集:统计已生成草稿的模板化程度(只读,不改写作实现)。

用法:
    python scripts/template_probe.py [--out path.json]

口径见 ``ttxt.writing.style.template_signals``;跨稿共享段首单独统计,
用来区分"单篇内部重复"和"所有稿子同一个开头"这两类模板化。
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

from ttxt import config as cfg
from ttxt.db import connect
from ttxt.writing.style import template_signals

CATEGORIES = ("technology", "finance", "health", "history", "social")


def _opening_of_first_sentence(content: str) -> str:
    for s in content.replace("\r", "").split("\n"):
        s = s.strip()
        if len(s) >= 10:
            return s[:8]
    return ""


def probe_category(category: str) -> dict:
    con = connect(cfg.category_db_path(category))
    try:
        rows = con.execute(
            "SELECT draft_id, version, content, model_id, created_at"
            " FROM writing_history ORDER BY rowid"
        ).fetchall()
    finally:
        con.close()
    drafts = []
    all_openings: Counter[str] = Counter()
    markers: Counter[str] = Counter()
    first_openings: Counter[str] = Counter()
    for row in rows:
        content = row["content"] or ""
        sig = template_signals(content)
        drafts.append(
            {
                "draft_id": row["draft_id"],
                "version": row["version"],
                "chars": len(content),
                "model_id": row["model_id"],
                **sig,
            }
        )
        for key, val in sig["markers"].items():
            markers[key] += val
        for opening, hits in sig["repeated_openings"].items():
            all_openings[opening] += hits
        first = _opening_of_first_sentence(content)
        if first:
            first_openings[first] += 1
        drafts[-1]["first_opening"] = first
    sentences = sum(d["sentences"] for d in drafts)
    marker_hits = sum(d["marker_hits"] for d in drafts)
    shared = {k: v for k, v in all_openings.items() if v >= 2}
    cvs = [d["sentence_length_cv"] for d in drafts if d["sentence_length_cv"] is not None]
    return {
        "category": category,
        "drafts": len(drafts),
        "sentences": sentences,
        "marker_hits": marker_hits,
        "marker_hits_per_100_sentences": round(marker_hits * 100 / sentences, 2) if sentences else 0.0,
        "top_markers": dict(markers.most_common(8)),
        "drafts_with_any_marker": sum(1 for d in drafts if d["marker_hits"]),
        "drafts_with_repeated_opening": sum(1 for d in drafts if d["repeated_opening_kinds"]),
        "duplicate_sentence_share_mean": round(
            statistics.fmean(d["duplicate_sentence_share"] for d in drafts), 4) if drafts else 0.0,
        "parallel_pattern_hits": sum(d["parallel_pattern_hits"] for d in drafts),
        "sentence_length_cv_mean": round(statistics.fmean(cvs), 4) if cvs else None,
        "cross_draft_shared_openings": dict(sorted(shared.items(), key=lambda kv: -kv[1])[:10]),
        "cross_draft_shared_opening_kinds": len(shared),
        "first_sentence_openings": dict(first_openings.most_common(10)),
        "first_sentence_opening_repeat_kinds": sum(1 for v in first_openings.values() if v >= 2),
        "rows": drafts,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    result = {"generated_at": cfg.now_iso(), "categories": {}}
    for category in CATEGORIES:
        if cfg.category_db_path(category).exists():
            result["categories"][category] = probe_category(category)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
