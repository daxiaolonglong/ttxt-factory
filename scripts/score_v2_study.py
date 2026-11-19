"""R45:score_v2 拟合与并排评估(只读旧实现,不改一行)。

产出:03_intelligence/score_v2_study-20261119.json
用法:python scripts/score_v2_study.py
"""
from __future__ import annotations

import heapq
import json
import math
import random
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path

from ttxt.analysis import prediction, score_v2, viral

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = ("technology", "finance", "health", "history", "social")
OUT = ROOT / "03_intelligence" / "score_v2_study-20261119.json"
BOOTSTRAP = 200
SEED = 42


def load_rows() -> list[dict]:
    rows: list[dict] = []
    for category in CATEGORIES:
        db = ROOT / "01_categories" / category / "articles.db"
        if not db.is_file():
            continue
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            for row in con.execute(
                "SELECT a.id, a.category, a.title, a.content, a.published_at, m.reads"
                " FROM articles a JOIN article_metrics m ON m.article_id = a.id"
                " WHERE m.reads IS NOT NULL AND m.reads > 0"
            ):
                rows.append(
                    {
                        "id": int(row["id"]),
                        "category": row["category"] or category,
                        "title": row["title"] or "",
                        "content": row["content"] or "",
                        "published_at": row["published_at"],
                        "reads": int(row["reads"]),
                    }
                )
        finally:
            con.close()
    return rows


def measured_baselines() -> dict[str, dict[str, float]]:
    """旧 prediction 口径的桶均值基线(每类目),只用于并排对照,不改旧实现。"""
    result: dict[str, dict[str, float]] = {}
    for category in CATEGORIES:
        db = ROOT / "01_categories" / category / "articles.db"
        if not db.is_file():
            continue
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            buckets = prediction._measured_baselines(con)
        finally:
            con.close()
        means = {key: statistics.fmean(values) for key, values in buckets.items() if values}
        result[category] = means
    return result


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return float("nan")
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else float("nan")


def fisher_ci(rho: float, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n < 4 or math.isnan(rho) or abs(rho) >= 1.0:
        return None
    transformed = math.atanh(rho)
    half = z / math.sqrt(n - 3)
    return math.tanh(transformed - half), math.tanh(transformed + half)


def lift_with_ci(scores: list[float], hits: list[int], n_boot: int = BOOTSTRAP, seed: int = SEED) -> dict:
    pairs = [(s, h) for s, h in zip(scores, hits) if not math.isnan(s)]
    n = len(pairs)
    if n < 30:
        return {"n": n, "status": "insufficient_sample"}
    scores = [p[0] for p in pairs]
    hits = [p[1] for p in pairs]
    cut = max(1, int(round(n * 0.1)))
    order = heapq.nlargest(cut, range(n), key=lambda i: scores[i])
    precision = sum(hits[i] for i in order) / cut
    base = sum(hits) / n
    lift = precision / base if base else float("nan")
    rng = random.Random(seed)
    values = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        sample_scores = [scores[i] for i in idx]
        sample_hits = [hits[i] for i in idx]
        chosen = heapq.nlargest(cut, range(n), key=lambda i: sample_scores[i])
        p = sum(sample_hits[i] for i in chosen) / cut
        b = sum(sample_hits) / n
        values.append(p / b if b else 0.0)
    values.sort()
    return {
        "n": n,
        "base_rate": round(base, 6),
        "precision_top10pct": round(precision, 6),
        "lift": round(lift, 4),
        "lift_ci95": [round(values[int(0.025 * len(values))], 4), round(values[int(0.975 * len(values)) - 1], 4)],
    }


def _combined_lift(score_groups: list[list[float]], hit_groups: list[list[int]]) -> tuple[float, float, int]:
    """合并多个类目的类目内 top-decile lift(按样本量加权),返回 (lift, precision, n)。"""
    total_n = total_cut = hit_in_cut = total_hits = 0
    for scores, hits in zip(score_groups, hit_groups):
        n = len(scores)
        cut = max(1, int(round(n * 0.1)))
        chosen = heapq.nlargest(cut, range(n), key=lambda i: scores[i])
        total_n += n
        total_cut += cut
        hit_in_cut += sum(hits[i] for i in chosen)
        total_hits += sum(hits)
    if not total_cut or not total_n or not total_hits:
        return float("nan"), 0.0, total_n
    precision = hit_in_cut / total_cut
    base = total_hits / total_n
    return (precision / base if base else float("nan")), precision, total_n


def lift_micro_with_ci(score_groups: list[list[float]], hit_groups: list[list[int]],
                       n_boot: int = BOOTSTRAP, seed: int = SEED) -> dict:
    lift, precision, n = _combined_lift(score_groups, hit_groups)
    if n < 30:
        return {"n": n, "status": "insufficient_sample"}
    rng = random.Random(seed)
    values = []
    for _ in range(n_boot):
        sampled_scores, sampled_hits = [], []
        for scores, hits in zip(score_groups, hit_groups):
            size = len(scores)
            idx = [rng.randrange(size) for _ in range(size)]
            sampled_scores.append([scores[i] for i in idx])
            sampled_hits.append([hits[i] for i in idx])
        value, _p, _n = _combined_lift(sampled_scores, sampled_hits)
        values.append(0.0 if math.isnan(value) else value)
    values.sort()
    return {
        "n": n,
        "precision_top10pct": round(precision, 6),
        "lift": round(lift, 4),
        "lift_ci95": [round(values[int(0.025 * len(values))], 4), round(values[int(0.975 * len(values)) - 1], 4)],
        "scope": "pooled over categories with n_test>=200, within-category deciles",
    }


def main() -> None:
    rows = load_rows()
    parsed = [row for row in rows if row.get("published_at")]
    train, test = score_v2.time_split(parsed, train_ratio=0.7)
    print(f"rows={len(rows)} dated={len(parsed)} train={len(train)} test={len(test)}")

    category_median = {}
    for category in {row["category"] for row in train}:
        category_median[category] = statistics.median(
            math.log10(row["reads"]) for row in train if row["category"] == category
        )
    fallback = statistics.median(math.log10(row["reads"]) for row in train)

    def target(row: dict) -> float:
        return math.log10(row["reads"]) - category_median.get(row["category"], fallback)

    train_features = [score_v2.extract_features(row["title"], row["content"], row["category"]) for row in train]
    model = score_v2.fit(train_features, [target(row) for row in train])

    test_features = [score_v2.extract_features(row["title"], row["content"], row["category"]) for row in test]
    v2_scores = [score_v2.predict(model, features) for features in test_features]

    # 类目内阅读分位(评估口径,不是特征)
    by_category: dict[str, list[dict]] = {}
    for row in test:
        by_category.setdefault(row["category"], []).append(row)
    percentiles: dict[int, float] = {}
    for category, members in by_category.items():
        ordered = sorted(members, key=lambda r: r["reads"])
        n = len(ordered)
        for position, member in enumerate(ordered):
            percentiles[member["id"]] = position / max(n - 1, 1)
    pct = [percentiles[row["id"]] for row in test]

    old_reads_scores = [row["reads"] / (row["reads"] + 1000.0) for row in test]
    baselines = measured_baselines()
    old_bucket_scores: list[float] = []
    bucket_covered = 0
    for row in test:
        legacy = viral.compute_features(row["title"], row["content"], len(row["content"] or ""))
        bucket = prediction._bucket(legacy)
        value = (baselines.get(row["category"]) or {}).get(bucket)
        if value is None:
            value = (baselines.get(row["category"]) or {}).get("all")
        old_bucket_scores.append(float(value) if value is not None else float("nan"))
        bucket_covered += 0 if value is None else 1

    hits = [1 if value >= 0.9 else 0 for value in pct]

    def spearman_block(scores: list[float], subset: list[int]) -> dict:
        idx = [i for i in subset if not math.isnan(scores[i])]
        xs = [scores[i] for i in idx]
        ys = [pct[i] for i in idx]
        rho = spearman(xs, ys)
        ci = fisher_ci(rho, len(idx))
        return {
            "n": len(idx),
            "spearman": None if math.isnan(rho) else round(rho, 4),
            "ci95": None if ci is None else [round(ci[0], 4), round(ci[1], 4)],
        }

    all_idx = list(range(len(test)))
    per_category = {}
    for category, members in by_category.items():
        idx = [i for i, row in enumerate(test) if row["category"] == category]
        per_category[category] = {
            "v2": {**spearman_block(v2_scores, idx), "top_decile": lift_with_ci(
                [v2_scores[i] for i in idx], [hits[i] for i in idx])},
            "old_bucket": {**spearman_block(old_bucket_scores, idx), "top_decile": lift_with_ci(
                [old_bucket_scores[i] for i in idx], [hits[i] for i in idx])},
            "old_reads_score": {**spearman_block(old_reads_scores, idx), "top_decile": lift_with_ci(
                [old_reads_scores[i] for i in idx], [hits[i] for i in idx])},
        }

    # 类目内 top-decile:池化只合并测试样本 ≥200 的类目(小类目按不足报)
    big_categories = [c for c, rows in by_category.items() if len(rows) >= 200]
    big_idx = {c: [i for i, row in enumerate(test) if row["category"] == c] for c in big_categories}
    eval_block = {}
    for key, scores in (("v2", v2_scores), ("old_bucket", old_bucket_scores),
                        ("old_reads_score", old_reads_scores)):
        eval_block[key] = {
            **spearman_block(scores, all_idx),
            "top_decile": lift_micro_with_ci(
                [[scores[i] for i in big_idx[c]] for c in big_categories],
                [[hits[i] for i in big_idx[c]] for c in big_categories],
            ),
            "distinct_values": len({round(s, 6) for s in scores if not math.isnan(s)}),
        }

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "version": score_v2.VERSION,
        "target": "log10(reads) - 训练集类目中位数(去偏);特征不含任何结果类字段",
        "sample": {
            "rows_with_reads": len(rows),
            "dated": len(parsed),
            "train": len(train),
            "test": len(test),
            "train_published_max": max(row["published_at"] for row in train),
            "test_published_min": min(row["published_at"] for row in test),
            "category_median_log10_reads_train": {k: round(v, 4) for k, v in category_median.items()},
        },
        "model": model,
        "evaluation": {"pooled": eval_block, "per_category": per_category},
        "pooled_lift_categories": big_categories,
        "old_bucket_coverage": {
            "covered": bucket_covered,
            "total": len(test),
            "distinct_values": len({v for v in old_bucket_scores if not math.isnan(v)}),
        },
        "notes": [
            "old_reads_score = reads/(reads+1000),是按读取数定义的单调映射;其 Spearman≈1 是构造使然,不是预测力证据(泄漏)。",
            "old_bucket 仅覆盖有 measured 基线的桶;样本不足按不足报,不凑样本。",
            "v2 的 Spearman 与 top-decile lift 都按类目内阅读分位计算,消除类目量级差。",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"pooled": eval_block, "old_bucket_coverage": payload["old_bucket_coverage"],
                      "coefficients": model["coefficients_raw"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
