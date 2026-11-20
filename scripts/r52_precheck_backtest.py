"""R52:选题预筛阈值回溯 —— 用 R50 的 18 run 历史数据检验预筛打分能不能预测 pass/fail。

口径(如实登记的局限):
- 标签:`03_intelligence/pass_rate_probe-20261119-r50.json` 的 18 run passed 字段;
- 打分:`ttxt.writing.precheck.precheck_topic` 在 **R50 探针当时的类目库快照**
  (`07_tmp/r50_passrate/<cat>_snap.db`)上计算 —— 材料池与那 18 run 当时看到的完全一致,
  不受快照之后语料增长污染;
- 主指标 score = 跨独立来源(≥2)印证的可绑定一手锚点数(先于回溯声明,见 precheck.py);
- 6 选题 × 3 轮:同一选题三轮分数相同,run 级 AUC 的变异全部来自选题间差异,
  选题内 pass/fail 翻转(生成随机性)会压低 AUC —— 这是预筛层天然的天花板,如实计入;
- 阈值扫描:对全部观测分数做 sweep,给混淆矩阵与漏杀/误放计数;报告不挑最好看的数,
  同时给 AUC 判定(接近 0.5 ⇒ 预筛不成立,写负结果收手)。

只读:不写类目库、不调模型、不碰门禁。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ttxt.writing import precheck  # noqa: E402

PROBE = ROOT / "03_intelligence" / "pass_rate_probe-20261119-r50.json"
SNAP_DIR = ROOT / "07_tmp" / "r50_passrate"
OUT = ROOT / "03_intelligence" / "r52_precheck_backtest-20261120.json"


def auc(scores: list[float], labels: list[int]) -> dict:
    """AUC(Mann-Whitney):score 预测 pass(label=1)的排序能力,并列记 0.5。"""
    pos = [(s, i) for i, (s, l) in enumerate(zip(scores, labels)) if l == 1]
    neg = [(s, i) for i, (s, l) in enumerate(zip(scores, labels)) if l == 0]
    if not pos or not neg:
        return {"auc": None, "note": "单侧类别为空,AUC 不可定义"}
    wins = 0.0
    for sp, _ in pos:
        for sn, _ in neg:
            if sp > sn:
                wins += 1.0
            elif sp == sn:
                wins += 0.5
    return {"auc": round(wins / (len(pos) * len(neg)), 4), "n_pos": len(pos), "n_neg": len(neg)}


def confusion(scores: list[float], labels: list[int], threshold: float) -> dict:
    """预筛 score < threshold ⇒ 挡(blocked)。
    漏杀 = 挡了但本来会过(label=1);误放 = 放了但实际挂(label=0)。"""
    tp = fp = fn = tn = 0  # 以"挡下必挂的"为正向
    missed_pass = 0   # 漏杀
    released_fail = 0  # 误放
    for s, l in zip(scores, labels):
        blocked = s < threshold
        if blocked and l == 0:
            tp += 1
        elif blocked and l == 1:
            fn += 1
            missed_pass += 1
        elif not blocked and l == 0:
            fp += 1
            released_fail += 1
        else:
            tn += 1
    return {
        "threshold": threshold,
        "blocked_would_fail": tp,
        "blocked_would_pass_漏杀": fn,
        "released_did_fail_误放": fp,
        "released_would_pass": tn,
        "total_runs": len(scores),
    }


def main() -> int:
    probe = json.loads(PROBE.read_text(encoding="utf-8"))
    runs = probe["runs"]
    batch = probe["batch"]

    topic_scores: dict[tuple[str, str], dict] = {}
    for item in batch:
        cat, topic = item["category"], item["topic"]
        snap = SNAP_DIR / f"{cat}_snap.db"
        if not snap.exists():
            print(f"missing snapshot {snap}", file=sys.stderr)
            return 2
        pre = precheck.precheck_topic(cat, topic, db_path=snap)
        pre.pop("material_stats", None)  # 输出瘦身:取材读数已在 probe/日志里
        topic_scores[(cat, topic)] = pre

    scores = []
    labels = []
    run_rows = []
    for r in runs:
        key = (r["category"], r["topic"])
        pre = topic_scores.get(key)
        if pre is None or r.get("passed") is None:
            continue
        scores.append(float(pre["score"]))
        labels.append(1 if r["passed"] else 0)
        run_rows.append({
            "category": r["category"], "topic": r["topic"], "round": r["round"],
            "score": pre["score"], "first_hand": pre["first_hand_total"],
            "passed": bool(r["passed"]), "primary": r.get("primary"),
        })

    a_score = auc(scores, labels)
    # 次指标(对照,不作决策依据):first_hand_total
    fh_scores = [float(r["first_hand"]) for r in run_rows]
    a_fh = auc(fh_scores, labels)

    # 阈值扫描:所有观测到的 distinct score 及其 +1 处
    distinct = sorted({s for s in scores})
    sweep = []
    for t in distinct:
        sweep.append(confusion(scores, labels, t + 0.5))
    best = max(
        sweep,
        key=lambda c: (c["blocked_would_fail"] + c["released_would_pass"]) / max(1, c["total_runs"]),
    )

    topic_rows = []
    for (cat, topic), pre in topic_scores.items():
        sub = [r for r in run_rows if r["category"] == cat and r["topic"] == topic]
        topic_rows.append({
            "category": cat, "topic": topic, "score": pre["score"],
            "first_hand_total": pre["first_hand_total"],
            "cross_by_dim": pre["cross_by_dim"],
            "first_hand_by_dim": pre["first_hand_by_dim"],
            "full_bodies_selected": pre["full_bodies_selected"],
            "distinct_source_keys": pre["distinct_source_keys"],
            "runs_passed": sum(1 for r in sub if r["passed"]),
            "runs_total": len(sub),
            "thin_dims": pre["thin_dims"],
        })

    doc = {
        "note": "R52 预筛回溯:打分基于 R50 探针快照库(材料池与 18 run 当时一致),标签=R50 passed",
        "primary_metric": "cross_verified_total(先声明,见 precheck.py 文档)",
        "n_runs": len(scores),
        "n_topics": len(topic_rows),
        "auc_primary": a_score,
        "auc_first_hand_secondary": a_fh,
        "threshold_sweep": sweep,
        "best_threshold_by_accuracy": best,
        "topics": sorted(topic_rows, key=lambda t: t["score"]),
        "runs": run_rows,
    }
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print(f"AUC(主指标 cross_verified): {a_score}")
    print(f"AUC(对照 first_hand): {a_fh}")
    print("\n选题分数:")
    for t in doc["topics"]:
        print(f"  [{t['category']}] {t['topic']} | score={t['score']} first_hand={t['first_hand_total']}"
              f" cross{t['cross_by_dim']} first{t['first_hand_by_dim']}"
              f" | passed {t['runs_passed']}/{t['runs_total']}")
    print("\n阈值扫描(score < T ⇒ 挡):")
    for c in sweep:
        print(f"  T>{c['threshold']:.1f}: 挡下必挂 {c['blocked_would_fail']} | 漏杀 {c['blocked_would_pass_漏杀']}"
              f" | 误放 {c['released_did_fail_误放']} | 放行且过 {c['released_would_pass']}")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
