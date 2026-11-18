"""R40 只读分析:现有文章评分与真实表现分的相关性对照。

口径(不改任何实现、不改门禁):
- **表现分**复用现有 ``viral_labels.score``,但严格分池:
  - ``measured``:曝光/阅读/点赞/评论齐全,分数=加权互动率(0-1);
  - ``measured_reads``:只有阅读量的池,分数=reads/(reads+1000) 的单调映射。
  两个池的尺子不同,只分别统计,**绝不合并**。
- **文章评分**取现有 ``prediction_records.predicted_score``(文本特征分桶均值基线)。
  主口径只认管线已回填的 ``outcome_score``(= measured 池真值);
  敏感性口径另行统计 predicted 与 ``measured_reads`` 真值的重叠,单独标注、不作为管线结论。
- 样本不足以支撑结论时直接报 ``insufficient_sample``,不外推。

用法:
    python scripts/score_vs_performance.py [--out path.json]

退出码:0 = 分析完成(无论结论是否显著);2 = 无任何可对照样本。
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from ttxt import config as cfg
from ttxt.db import connect

CATEGORIES = ("technology", "finance", "health", "history", "social")
MIN_SAMPLES = 30  # 与文内其它统计口径一致:少于 30 只报告,不下结论


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """平均秩 + Pearson;有并列值时用平均秩,避免伪高相关。"""
    n = len(xs)
    if n < 3:
        return None

    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: values[i])
        out = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and values[order[j + 1]] == values[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return round(num / (dx * dy), 4)


def _group_compare(xs: list[float], ys: list[float]) -> dict:
    """按文章评分中位数分高低两组,比较表现分均值/中位数。"""
    n = len(xs)
    if n < 4:
        return {"n": n, "status": "insufficient_sample"}
    med = statistics.median(xs)
    high = [y for x, y in zip(xs, ys) if x > med]
    low = [y for x, y in zip(xs, ys) if x < med]
    tie = n - len(high) - len(low)
    out = {
        "n": n,
        "median_split": round(med, 4),
        "high_group": {"n": len(high), "mean": round(statistics.fmean(high), 4) if high else None},
        "low_group": {"n": len(low), "mean": round(statistics.fmean(low), 4) if low else None},
        "tied_at_median": tie,
    }
    if high and low:
        out["mean_diff"] = round(statistics.fmean(high) - statistics.fmean(low), 4)
    return out


def _bucket_table(pairs: list[dict]) -> dict:
    """把 basis 里的 bucket 还原,给出每个桶的预测均值 vs 实际均值。"""
    buckets: dict[str, dict[str, list[float]]] = {}
    for row in pairs:
        try:
            bucket = json.loads(row.get("basis") or "{}").get("bucket", "unknown")
        except (TypeError, ValueError):
            bucket = "unknown"
        slot = buckets.setdefault(bucket, {"pred": [], "out": []})
        slot["pred"].append(float(row["predicted_score"]))
        slot["out"].append(float(row["outcome_score"]))
    return {
        key: {
            "n": len(val["pred"]),
            "predicted_mean": round(statistics.fmean(val["pred"]), 4),
            "outcome_mean": round(statistics.fmean(val["out"]), 4),
            "distinct_predicted": len({round(v, 6) for v in val["pred"]}),
        }
        for key, val in sorted(buckets.items())
    }


def analyse_category(category: str) -> dict:
    con = connect(cfg.category_db_path(category))
    try:
        tier_rows = con.execute(
            "SELECT confidence_tier, COUNT(*) AS n, AVG(score) AS mean_score,"
            " MIN(score) AS min_score, MAX(score) AS max_score"
            " FROM viral_labels WHERE score IS NOT NULL GROUP BY confidence_tier"
        ).fetchall()
        pools = {
            row["confidence_tier"]: {
                "cards": row["n"],
                "mean_score": round(row["mean_score"], 4),
                "min_score": round(row["min_score"], 4),
                "max_score": round(row["max_score"], 4),
            }
            for row in tier_rows
        }
        pairs = [
            dict(row)
            for row in con.execute(
                "SELECT subject, predicted_score, outcome_score, basis, predicted_at, outcome_at"
                " FROM prediction_records WHERE outcome_score IS NOT NULL ORDER BY id"
            )
        ]
        pred_total = con.execute("SELECT COUNT(*) FROM prediction_records").fetchone()[0]

        xs = [float(p["predicted_score"]) for p in pairs]
        ys = [float(p["outcome_score"]) for p in pairs]
        main = {
            "basis": "prediction_records.outcome_score(measured 池回填)",
            "n": len(pairs),
            "distinct_predicted": len({round(v, 6) for v in xs}),
            "distinct_outcome": len({round(v, 6) for v in ys}),
            "spearman": _spearman(xs, ys),
            "group_compare": _group_compare(xs, ys),
            "buckets": _bucket_table(pairs),
            "mae": round(statistics.fmean(abs(a - b) for a, b in zip(xs, ys)), 4) if pairs else None,
            "mean_bias": round(statistics.fmean(a - b for a, b in zip(xs, ys)), 4) if pairs else None,
            "status": "ok" if len(pairs) >= MIN_SAMPLES else "insufficient_sample",
            "min_samples": MIN_SAMPLES,
            "correlation_meaning": "秩相关,只描述本样本内的次序吻合度;n<30 不外推",
        }
        if len(pairs) < MIN_SAMPLES:
            main["note"] = f"已回填 {len(pairs)} 对 < {MIN_SAMPLES},只能报描述统计,不得宣称评分能预测表现"

        # 敏感性:predicted 与 measured_reads 池真值的重叠(不同尺子,单独记账)
        sens_rows = []
        for row in con.execute(
            "SELECT pr.subject AS subject, pr.predicted_score AS predicted_score,"
            " v.score AS outcome_score, v.confidence_tier AS tier"
            " FROM prediction_records pr"
            " JOIN viral_labels v ON v.article_id = CAST(SUBSTR(pr.subject, 9) AS INTEGER)"
            " WHERE pr.subject LIKE 'article:%' AND v.confidence_tier='measured_reads'"
            " AND v.score IS NOT NULL"
        ):
            sens_rows.append(dict(row))
        sx = [float(r["predicted_score"]) for r in sens_rows]
        sy = [float(r["outcome_score"]) for r in sens_rows]
        sensitivity = {
            "basis": "predicted_score × measured_reads 池(阅读量单调映射,与主口径不同尺子)",
            "n": len(sens_rows),
            "spearman": _spearman(sx, sy),
            "group_compare": _group_compare(sx, sy),
            "counts_as_pipeline_outcome": False,
            "note": "敏感性分析,不改变 settle 规则、不构成管线结论",
        }

        return {
            "category": category,
            "articles": con.execute("SELECT COUNT(*) FROM articles").fetchone()[0],
            "performance_pools": pools,
            "prediction_records": pred_total,
            "main": main,
            "sensitivity_measured_reads": sensitivity,
        }
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    result = {"generated_at": cfg.now_iso(), "min_samples": MIN_SAMPLES, "categories": {}}
    for category in CATEGORIES:
        if cfg.category_db_path(category).exists():
            result["categories"][category] = analyse_category(category)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    paired = sum(v["main"]["n"] for v in result["categories"].values())
    return 0 if paired else 2


if __name__ == "__main__":
    raise SystemExit(main())
