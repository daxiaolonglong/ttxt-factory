"""R48 T-10:同一批选题跑"改前(关闭去模板化)/改后(开启)"真实对照。

口径(如实记账,不做任何挑选):
- 两个模式对每个选题各跑一次完整写作链路(经 model_gate 串行闸,本地 127.0.0.1:8080);
- 素材库用**快照副本**:先 sqlite `backup` 到临时目录,写作历史只写进副本,
  不污染正式类目库(副本上跑同样满足取材门禁,取材内容与正式库一致);
- 读数取每个模式**最终版本正文**的 template_signals:套话/百句、并列句式/百句、
  句长变异系数;类目内与全批的均值/中位/极值一并给出;
- 改后模式还回账 template_check 状态(through / still_over_budget / rewrite_failed /
  not_available)与遗留标记;被既有门禁拦下(status=rejected)的稿子照测照记,不剔除。

用法:
    python scripts/template_decheck_compare.py --batch batch.json [--out 03_intelligence/template_decheck_compare-YYYYMMDD.json]
batch.json 形如:[{"category": "technology", "topic": "..."}, ...]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import statistics
import time
from pathlib import Path

from ttxt import config as cfg
from ttxt.crawler import store
from ttxt.writing import workflow
from ttxt.writing.style import template_signals

MODES = ("before", "after")
SIG_KEYS = ("sentences", "marker_hits", "parallel_pattern_hits",
            "repeated_sentence_kinds", "repeated_opening_kinds")


def _final_text(db_snapshot: Path, draft_id: str) -> str:
    con = sqlite3.connect(db_snapshot)
    try:
        row = con.execute(
            "SELECT content FROM writing_history WHERE draft_id=? ORDER BY version DESC LIMIT 1",
            (draft_id,),
        ).fetchone()
    finally:
        con.close()
    return row[0] if row else ""


def _aggregate(runs: list[dict]) -> dict:
    out: dict = {"runs": len(runs)}
    for key, series in (
        ("marker_per_100_sent", [100.0 * r["signals"]["marker_hits"] / r["signals"]["sentences"] for r in runs if r["signals"]["sentences"]]),
        ("parallel_per_100_sent", [100.0 * r["signals"]["parallel_pattern_hits"] / r["signals"]["sentences"] for r in runs if r["signals"]["sentences"]]),
        ("parallel_hits_abs", [r["signals"]["parallel_pattern_hits"] for r in runs]),
        ("sentence_length_cv", [r["signals"]["sentence_length_cv"] for r in runs if r["signals"]["sentence_length_cv"] is not None]),
    ):
        if series:
            out[key] = {
                "mean": round(statistics.fmean(series), 3),
                "median": round(statistics.median(series), 3),
                "min": round(min(series), 3),
                "max": round(max(series), 3),
                "n": len(series),
            }
    for key in ("marker_hits", "repeated_sentence_kinds", "repeated_opening_kinds"):
        out[key + "_total"] = sum(r["signals"][key] for r in runs)
    out["check_status"] = {s: sum(1 for r in runs if r.get("check_status") == s)
                           for s in ("within_budget", "through", "still_over_budget",
                                     "rewrite_failed", "not_available", "insufficient_sentences", None)}
    out["rewrite_calls_total"] = sum(int((r.get("template_check") or {}).get("rewrite_count") or 0) for r in runs)
    out["flags_total"] = sum(len(r.get("template_flags") or []) for r in runs)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--work", default=str(cfg.PROJECT_ROOT / "07_tmp" / "r48_compare"))
    args = ap.parse_args()

    batch = json.loads(Path(args.batch).read_text(encoding="utf-8"))
    stamp = time.strftime("%Y%m%d")
    out_path = Path(args.out) if args.out else cfg.PROJECT_ROOT / "03_intelligence" / f"template_decheck_compare-{stamp}.json"
    work = Path(args.work)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    doc = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "modes": {
            "before": "template_decheck=False(改前:生成后不做去模板化检查)",
            "after": "template_decheck=True(改后:超类目预算段落经本地模型定向重写一次,仍超标只标记)",
        },
        "budget_file": str(cfg.PROJECT_ROOT / "00_control" / "template_budgets.json"),
        "model": workflow.cfg.load_config().get("ollama", {}).get("model"),
        "batch": batch,
        "runs": [],
    }

    for item in batch:
        cat, topic = item["category"], item["topic"]
        snap = work / f"{cat}_snap.db"
        if not snap.exists():  # 每个类目快照一次,两模式共用同一份素材快照
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
        for mode in MODES:
            t0 = time.time()
            try:
                res = workflow.write_article(
                    cat, topic,
                    export_dir=work / "exports" / mode,
                    db_path=snap,
                    template_decheck=(mode == "after"),
                )
                # 取材门禁等分支会在写稿前提前返回,没有 draft_id;照记状态,不给假读数
                did = res.get("draft_id")
                run = {
                    "category": cat, "topic": topic, "mode": mode,
                    "draft_id": did, "status": res.get("status"),
                    "passed": res.get("passed"), "versions": res.get("versions"),
                    "score": res.get("score"),
                    "fact_check_rate": (res.get("fact_checkability") or {}).get("rate"),
                    "early_return_reason": None if did else json.dumps(
                        {k: v for k, v in res.items()
                         if k not in ("materials", "claims", "review", "fact_check")},
                        ensure_ascii=False)[:500],
                    "check_status": (res.get("template_check") or {}).get("check_status"),
                    "template_check": res.get("template_check"),
                    "template_flags": res.get("template_flags"),
                    "elapsed_s": round(time.time() - t0, 1),
                }
                if did:
                    sig = template_signals(_final_text(snap, did))
                    run["signals"] = {k: sig[k] for k in SIG_KEYS} | {"sentence_length_cv": sig["sentence_length_cv"]}
            except Exception as exc:  # noqa: BLE001 - 对照实验必须把失败也照记
                run = {"category": cat, "topic": topic, "mode": mode, "error": str(exc)[:300],
                       "elapsed_s": round(time.time() - t0, 1)}
            doc["runs"].append(run)
            print(f"[{mode}] {cat} | {topic} | {run.get('status') or run.get('error')} | check={run.get('check_status')}", flush=True)

    for mode in MODES:
        runs = [r for r in doc["runs"] if r["mode"] == mode and "signals" in r]
        doc.setdefault("aggregate", {})[mode] = _aggregate(runs)
        by_cat: dict[str, dict] = {}
        for cat in {r["category"] for r in runs}:
            by_cat[cat] = _aggregate([r for r in runs if r["category"] == cat])
        doc["aggregate"][mode]["by_category"] = by_cat

    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
