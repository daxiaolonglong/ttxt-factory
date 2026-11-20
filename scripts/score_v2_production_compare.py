"""R47:正式链路并列对照读数(旧 viral_labels vs 新 score_v2_labels,同一时间切分)。

口径:
- 样本:五类目 ``articles JOIN article_metrics``(reads>0)且 published_at 可解析;
- 切分:**全局**``time_split(0.7)``(五类目合并后按 published_at 升序一次切分),
  与 ``scripts/score_v2_fit_model.py`` 的拟合口径**完全相同**;读数只取 **holdout(未来 30%)**,
  模型没见过这些文章。报告登记 ``train_published_max`` / ``holdout_published_min`` 边界;
  分类目读数 = 全局 holdout 落在该类目内的行(不是类目内独立切分,避免训练泄漏);
- 旧读数:各库 ``viral_labels.score``(生产最近一轮 label_category 的落库值,原样读取,
  不重算不修改) —— 注意旧 score 由 ``score_with_metrics`` 生成,本身读取过结果类
  字段,这是"旧评分错位"的既有事实,并列展示但不假装可比;
- 新读数:各库 ``score_v2_labels.score_v2``(正式链路 apply 落库值,原样读取);
- 真实结果:文章实测 ``log10(reads)`` 的类目内分位(0~1);
- 指标:Spearman(分数 vs 实测分位)、top-decile lift(按分数取类目前 10%,其实测分位均值)、
  取值数(distinct)、样本量 n;Spearman 附 200 次 percentile bootstrap 95% CI。

用法:$env:PYTHONPATH='D:\\ttxt'; python scripts/score_v2_production_compare.py
产物:03_intelligence/score_v2_production_compare-20261119.json
"""
from __future__ import annotations

import json
import math
import random
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path

from ttxt.analysis import score_v2

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = ("technology", "finance", "health", "history", "social")
OUT = ROOT / "03_intelligence" / "score_v2_production_compare-20261119.json"
TRAIN_RATIO = 0.7
MIN_BOOT_N = 30


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    pos = (len(ordered) - 1) * q
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def ranks_with_ties(values: list[float]) -> list[float]:
    indexed = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and values[indexed[j + 1]] == values[indexed[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[indexed[k]] = avg
        i = j + 1
    return out


def actual_percentiles(values: list[float]) -> list[float]:
    """名次分位 (rank−0.5)/n,rank 为并列平均名次(1..n)。

    无偏:每个位置的分位恰为 (i−0.5)/n,全体均值**恰为 0.5**、随机选择期望 0.5,
    lift 的"1.0=无 lift"基准才成立。旧口径 rank/n 期望是 0.5+1/(2n),小样本下
    会把 lift 系统性抬高(如 n=12 抬 4.2%)。
    """
    n = len(values)
    return [(rank - 0.5) / n for rank in ranks_with_ties(values)]


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    rx, ry = ranks_with_ties(xs), ranks_with_ties(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if dx == 0.0 or dy == 0.0:
        return None
    return num / (dx * dy)


def bootstrap_ci(xs: list[float], ys: list[float], iters: int = 200) -> tuple[float, float] | None:
    n = len(xs)
    if n < MIN_BOOT_N:
        return None
    rng = random.Random(20261119)
    points = []
    for _ in range(iters):
        idx = [rng.randrange(n) for _ in range(n)]
        value = spearman([xs[i] for i in idx], [ys[i] for i in idx])
        if value is not None:
            points.append(value)
    if not points:
        return None
    return (round(percentile(points, 0.025), 4), round(percentile(points, 0.975), 4))


def load_category(category: str) -> list[dict]:
    db = ROOT / "01_categories" / category / "articles.db"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        rows = []
        v2_map: dict[int, float] = {}
        for row in con.execute(
            "SELECT a.id, a.published_at, m.reads, l.score AS old_score"
            " FROM articles a"
            " JOIN article_metrics m ON m.article_id = a.id"
            " LEFT JOIN viral_labels l ON l.article_id = a.id"
            " LEFT JOIN score_v2_labels s ON s.article_id = a.id"
            " WHERE m.reads IS NOT NULL AND m.reads > 0"
        ):
            rows.append(
                {
                    "id": int(row["id"]),
                    "category": category,
                    "published_at": row["published_at"],
                    "reads": int(row["reads"]),
                    "log_reads": math.log10(int(row["reads"])),
                    "old_score": row["old_score"],
                    "v2_score": None,
                }
            )
        for row in con.execute("SELECT article_id, score_v2 FROM score_v2_labels"):
            v2_map[int(row["article_id"])] = float(row["score_v2"])
        old_all = [
            row["old_score"]
            for row in con.execute("SELECT score AS old_score FROM viral_labels")
            if row["old_score"] is not None
        ]
        old_distinct = len({r[0] for r in con.execute("SELECT DISTINCT score FROM viral_labels")})
        for item in rows:
            item["v2_score"] = v2_map.get(item["id"])
        # 全表(非 holdout)取值数并列读数,直接说明"离散 vs 连续"
        rows_meta = {"old_distinct_full": int(old_distinct), "old_rows_full": len(old_all)}
        return rows, rows_meta
    finally:
        con.close()


def top_decile(pairs: list[tuple[float, float]]) -> dict | None:
    """按分数取前 10%,其"实测分位"均值 / 0.5(0.5=无 lift)。"""
    if len(pairs) < 10:
        return None
    k = max(1, int(len(pairs) * 0.1))
    ranked = sorted(pairs, key=lambda pair: pair[0], reverse=True)[:k]
    mean_pct = statistics.mean(pct for _, pct in ranked)
    return {"selected": k, "mean_actual_percentile": round(mean_pct, 4),
            "lift": round(mean_pct / 0.5, 3)}


def metrics_for(items: list[dict]) -> dict:
    """items:全局 holdout 落在同一类目内、v2 有分、reads 有值的行。"""
    out: dict = {"n": len(items)}
    if not items:
        return out
    actuals = [item["log_reads"] for item in items]
    # 名次分位 (rank−0.5)/n:均值恰为 0.5(无偏),lift 才有"1.0=无 lift"的基准;
    # min-max 归一化在偏态分布下期望不是 0.5,不能用。
    pcts = actual_percentiles(actuals)
    out["actual_log10_reads_p90"] = round(percentile(actuals, 0.9), 3)

    v2_scores = [item["v2_score"] for item in items]
    rho_v2 = spearman(v2_scores, pcts)
    out["v2"] = {
        "distinct_score_values": len(set(v2_scores)),
        "spearman_vs_actual_pct": None if rho_v2 is None else round(rho_v2, 4),
        "spearman_ci95": bootstrap_ci(v2_scores, pcts),
    }
    old_pairs = [(float(item["old_score"]), pct) for item, pct in zip(items, pcts) if item["old_score"] is not None]
    out["old"] = {"n_with_label": len(old_pairs),
                  "distinct_score_values": len({score for score, _ in old_pairs})}
    if old_pairs:
        rho_old = spearman([p[0] for p in old_pairs], [p[1] for p in old_pairs])
        out["old"]["spearman_vs_actual_pct"] = None if rho_old is None else round(rho_old, 4)
        out["old"]["spearman_ci95"] = bootstrap_ci([p[0] for p in old_pairs], [p[1] for p in old_pairs])

    # top-decile lift:按分数选类目前 10%,其"实测分位"均值(0.5=无 lift)
    out["v2"]["top_decile"] = top_decile(list(zip(v2_scores, pcts)))
    out["old"]["top_decile"] = top_decile(old_pairs)
    return out


def main() -> None:
    model_path = ROOT / "03_intelligence" / "score_v2_model.json"
    model = score_v2.load_model(model_path)
    # R51:产物必须能对上"哪次运行、哪一版模型"——生成时间与模型落盘时间都记账
    model_saved_at = json.loads(model_path.read_text(encoding="utf-8")).get("saved_at")
    # 先把五类目样本合并,做一次**全局** time_split —— 与拟合脚本完全相同的口径
    all_rows: list[dict] = []
    metas: dict[str, dict] = {}
    counts: dict[str, dict] = {}
    for category in CATEGORIES:
        rows, meta = load_category(category)
        metas[category] = meta
        dated = [row for row in rows if row.get("published_at")]
        counts[category] = {"rows_with_reads": len(rows), "dated": len(dated)}
        all_rows.extend(rows)

    train, test = score_v2.time_split(all_rows, train_ratio=TRAIN_RATIO)
    # id 只在类目库内唯一,互斥证明必须按 (类目, id) 对,不能只按 id
    train_keys = {(row["category"], row["id"]) for row in train}
    holdout_keys = {(row["category"], row["id"]) for row in test}
    overlap = train_keys & holdout_keys
    split_report = {
        "mode": "global",  # 五类目合并后一次切分,与拟合一致;类目内独立切分会让 holdout 混入训练见过文章
        "rows_all": len(all_rows),
        "dated": len(train) + len(test),
        "train": len(train),
        "holdout": len(test),
        "train_published_max": max(row["published_at"] for row in train),
        "holdout_published_min": min(row["published_at"] for row in test),
        "train_holdout_disjoint_proof": {
            "key": "(category, id)",
            "train_unique": len(train_keys),
            "holdout_unique": len(holdout_keys),
            "intersection": len(overlap),
            "disjoint": not overlap,
        },
    }

    report: dict = {
        "schema": "r47-score-v2-production-compare-v2",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model_saved_at": model_saved_at,
        "model_version": model["version"],
        "model_n_train": model["n_train"],
        "train_ratio": TRAIN_RATIO,
        "time_split": split_report,
        "metric_semantics": {
            "actual": "文章实测 log10(reads) 在类目全局-holdout 内的**名次分位 (rank−0.5)/n**"
                      "(均值恰为 0.5,随机选择期望 0.5;旧口径 rank/n 期望 0.5+1/(2n),已弃用)",
            "v2_source": "score_v2_labels(正式链路 apply 落库值,原样读取)",
            "old_source": "viral_labels.score(生产 label_category 最近一轮落库值;"
                          "旧实现读取过结果类字段,属既有错位,并列展示不假装可比)",
            "lift": "按分数取类目 top-decile,其实测分位均值 / 0.5",
        },
        "categories": {},
    }

    pooled_v2, pooled_old = [], []  # 全局:类目内分位已归一,可直接池化
    for category in CATEGORIES:
        holdout_all = [row for row in test if row["category"] == category]
        holdout = [row for row in holdout_all if row["v2_score"] is not None]
        cat_report = {
            **counts[category],
            "holdout_in_global": len(holdout_all),
            "holdout_scored": len(holdout),
            "full_table_distinct": metas[category],
            "holdout_metrics": metrics_for(holdout),
        }
        report["categories"][category] = cat_report
        if holdout:
            pcts = actual_percentiles([row["log_reads"] for row in holdout])
            for row, pct in zip(holdout, pcts):
                pooled_v2.append((row["v2_score"], pct))
                if row["old_score"] is not None:
                    pooled_old.append((float(row["old_score"]), pct))

    vx, vy = [p[0] for p in pooled_v2], [p[1] for p in pooled_v2]
    rho = spearman(vx, vy)
    report["pooled"] = {
        "n": len(pooled_v2),
        "v2": {"spearman": None if rho is None else round(rho, 4), "ci95": bootstrap_ci(vx, vy),
               "distinct": len(set(vx))},
        "old": {},
    }
    if len(pooled_old) >= 3:
        ox, oy = [p[0] for p in pooled_old], [p[1] for p in pooled_old]
        rho_o = spearman(ox, oy)
        report["pooled"]["old"] = {
            "n": len(pooled_old),
            "spearman": None if rho_o is None else round(rho_o, 4),
            "ci95": bootstrap_ci(ox, oy),
            "distinct": len(set(ox)),
        }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {OUT}")
    print(json.dumps(report["pooled"], ensure_ascii=False, indent=2))
    for category, block in report["categories"].items():
        hm = block["holdout_metrics"]
        print(category, json.dumps({"holdout": block["holdout_scored"],
                                    "v2": hm.get("v2", {}).get("spearman_vs_actual_pct"),
                                    "old": hm.get("old", {}).get("spearman_vs_actual_pct"),
                                    "v2_lift": (hm.get("v2", {}).get("top_decile") or {}).get("lift"),
                                    "old_lift": (hm.get("old", {}).get("top_decile") or {}).get("lift")},
                                   ensure_ascii=False))


if __name__ == "__main__":
    main()
