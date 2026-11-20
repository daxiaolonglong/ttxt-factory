"""R53:定点修复效果探针(在 R49/R50 探针口径上只加一件事:采集 targeted_repair 读数)。

口径:
- 同一批选题各跑 N 轮完整写作链路(model_gate 串行闸,本地 127.0.0.1:8080);
- 去模板化检查显式关闭(template_decheck=False),避免干扰通过率归因;
- 素材库用快照副本(sqlite backup),正式类目库零写入;
- 每个 run 按链路真实回账(status/notes/critical/truncated/score/fact_checkability)
  归类主阻断原因,规则命中数另计;失败样本原文片段照记;
- 额外照记回执里的 targeted_repair(修复成功率/调用增量),汇总三个数:
  一次修好/两轮修好/修不好、每条 claim 平均模型调用增量、单篇耗时(与 R50 r1 对比);
- 不动门禁、不动阈值、不动取样规则:本脚本只调用 write_article 并读回执。

用法:
    python scripts/r53_repair_probe.py --batch batch.json --rounds 1 \
        --out 03_intelligence/r53_repair_probe-YYYYMMDD.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
from pathlib import Path

from ttxt import config as cfg
from ttxt.crawler import store
from ttxt.writing import workflow

# 规则命中识别(基于 verifier/workflow 回写进 critical/notes 的固定文案)
RULE_MARKERS = {
    "claims提取为空(evidence_empty)": "事实证据链缺失(evidence_empty)",
    "伪造归属(attribution_not_in_source)": "伪造归属(attribution_not_in_source)",
    "无来源断言": "无来源断言:",
    "引句未绑定(quote未过0.34)": "原文引句未绑定",
    "事实锚点缺失(数字/日期/人物/机构/因果)": "事实未通过来源核验:",
}

PRIMARY_ORDER = (
    "material.insufficient",          # 取材前置门禁(素材不足)
    "gate.structure_truncated",       # 完整性/长度/截断
    "output.review_unparsable",       # 评审 JSON 解析失败(score=None)
    "output.claims_extraction_empty", # claims 提取为空/截断(evidence_empty)
    "fact.binding",                   # 事实绑定(引句未绑定/无来源/伪造归属)
    "fact.anchor_missing",            # 事实核验(锚点在来源找不到)
    "fact.checkability_below_line",   # 事实可核率 < 放行线
    "gate.score_below_min",           # 评审得分低于门禁
    "other",
)


def classify(res: dict) -> tuple[str, str]:
    """返回 (primary_reason, rule_detail)。顺序即优先级,只取第一因。"""
    if res.get("status") == "insufficient_materials":
        return "material.insufficient", res.get("notes") or ";".join(res.get("critical") or [])
    if res.get("truncated"):
        return "gate.structure_truncated", "内容完整性检查未通过(疑似截断)"
    if res.get("score") is None:
        return "output.review_unparsable", "评审输出无法解析,按不合格处理"
    criticals = res.get("critical") or []
    joined = ";".join(criticals)
    if "事实证据链缺失(evidence_empty)" in joined:
        return "output.claims_extraction_empty", joined[:300]
    if any(("原文引句未绑定" in c) or ("无来源断言" in c) or ("伪造归属(attribution_not_in_source)" in c) for c in criticals):
        return "fact.binding", joined[:300]
    if any("事实未通过来源核验:" in c for c in criticals):
        return "fact.anchor_missing", joined[:300]
    fc = res.get("fact_checkability") or {}
    if not fc.get("release_condition_met", True):
        return "fact.checkability_below_line", (
            f"事实可核率 {fc.get('rate')} < {fc.get('release_threshold')}"
            f"({fc.get('checkable')}/{fc.get('facts')})"
        )
    if criticals:
        return "other", joined[:300]
    return "gate.score_below_min", res.get("notes") or "评审得分低于门禁"


def rule_hits(criticals: list[str]) -> dict:
    counts: dict[str, int] = {}
    for c in criticals:
        for rule, marker in RULE_MARKERS.items():
            if marker in c:
                if rule == "事实锚点缺失(数字/日期/人物/机构/因果)" and "原文引句未绑定" in c:
                    continue  # 同一条文案里"引句未绑定"归上一条规则
                counts[rule] = counts.get(rule, 0) + 1
    return counts


def evidence_brief(res: dict) -> list[dict]:
    fc = res.get("fact_checkability") or {}
    rows = []
    for row in (fc.get("rows") or [])[:12]:
        rows.append({
            "claim": (row.get("claim") or "")[:80],
            "first_hand": row.get("first_hand"),
            "checkable": row.get("checkable"),
            "hits": (row.get("hits") or [])[:6],
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--work", default=str(cfg.PROJECT_ROOT / "07_tmp" / "r53_passrate"))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    batch = json.loads(Path(args.batch).read_text(encoding="utf-8"))
    stamp = time.strftime("%Y%m%d")
    out_path = Path(args.out) if args.out else cfg.PROJECT_ROOT / "03_intelligence" / f"r53_repair_probe-{stamp}.json"
    work = Path(args.work)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    conf = cfg.load_config()
    doc = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": "只读探针:不改门禁/阈值/取样规则;template_decheck 显式关闭;正式库零写入;写作链路启用 R53 定点修复并照记其读数",
        "gate_min_score": conf.get("writing", {}).get("gate_min_score"),
        "fact_checkability_min": workflow.FACT_CHECKABILITY_MIN,
        "model": conf.get("ollama", {}).get("model"),
        "batch": batch,
        "rounds": args.rounds,
        "runs": [],
    }

    for item in batch:
        cat, topic = item["category"], item["topic"]
        snap = work / f"{cat}_snap.db"
        if not snap.exists():
            src = cfg.category_db_path(cat)
            con = sqlite3.connect(src)
            try:
                dest = sqlite3.connect(snap)
                try:
                    con.backup(dest)
                finally:
                    dest.close()
            finally:
                con.close()
            store.migrate_articles_db(cat, db_path=snap)
        for rnd in range(1, args.rounds + 1):
            t0 = time.time()
            try:
                res = workflow.write_article(
                    cat, topic,
                    export_dir=work / "exports" / f"r{rnd}",
                    db_path=snap,
                    template_decheck=False,
                )
                primary, detail = classify(res)
                run = {
                    "category": cat, "topic": topic, "round": rnd,
                    "draft_id": res.get("draft_id"), "status": res.get("status"),
                    "passed": res.get("passed"), "versions": res.get("versions"),
                    "revisions": res.get("revisions"), "score": res.get("score"),
                    "truncated": res.get("truncated"),
                    "fact_check_rate": (res.get("fact_checkability") or {}).get("rate"),
                    "facts": (res.get("fact_checkability") or {}).get("facts"),
                    "checkable": (res.get("fact_checkability") or {}).get("checkable"),
                    "material_stats": res.get("material_stats"),
                    "notes": res.get("notes"),
                    "critical": res.get("critical") or [],
                    "rule_hits": rule_hits(res.get("critical") or []),
                    "primary": primary if not res.get("passed") else "passed",
                    "primary_detail": None if res.get("passed") else detail,
                    "evidence_brief": evidence_brief(res),
                    "targeted_repair": res.get("targeted_repair"),
                    "elapsed_s": round(time.time() - t0, 1),
                }
            except Exception as exc:  # noqa: BLE001 - 探针必须照记异常
                run = {"category": cat, "topic": topic, "round": rnd, "primary": "other",
                       "error": str(exc)[:300], "elapsed_s": round(time.time() - t0, 1)}
            doc["runs"].append(run)
            rep = run.get("targeted_repair") or {}
            print(f"[r{rnd}] {cat} | {topic} | {run.get('primary')} | score={run.get('score')} "
                  f"| fc={run.get('fact_check_rate')} "
                  f"| fix=1st:{rep.get('fixed_first', 0)}/2nd:{rep.get('fixed_second', 0)}/bad:{rep.get('unfixed', 0)}"
                  f" of fail:{rep.get('facts_failed', 0)} | {run.get('elapsed_s')}s", flush=True)
            out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    runs = doc["runs"]
    total = len(runs)
    dist: dict[str, int] = {}
    hits: dict[str, int] = {}
    for r in runs:
        dist[r.get("primary")] = dist.get(r.get("primary"), 0) + 1
        for rule, n in (r.get("rule_hits") or {}).items():
            hits[rule] = hits.get(rule, 0) + n
    doc["summary"] = {
        "total_runs": total,
        "passed": sum(1 for r in runs if r.get("passed")),
        "pass_rate": round(sum(1 for r in runs if r.get("passed")) / total, 4) if total else None,
        "primary_distribution": {
            k: {"n": n, "share": round(n / total, 4)} for k, n in
            sorted(dist.items(), key=lambda kv: -kv[1])
        },
        "rule_hit_totals": dict(sorted(hits.items(), key=lambda kv: -kv[1])),
        "by_category": {},
    }
    for cat in {r["category"] for r in runs}:
        sub = [r for r in runs if r["category"] == cat]
        d: dict[str, int] = {}
        for r in sub:
            d[r.get("primary")] = d.get(r.get("primary"), 0) + 1
        doc["summary"]["by_category"][cat] = {
            "runs": len(sub), "passed": sum(1 for r in sub if r.get("passed")), "primary": d,
        }
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
