"""R47:拟合并持久化 score_v2 正式模型产物(与 R45 已验证口径完全一致)。

口径(与 scripts/score_v2_study.py 相同,不另起炉灶):
- 样本:五类目 articles JOIN article_metrics 且 reads>0,必须带可解析 published_at;
- 时间切分:time_split(train_ratio=0.7),训练集严格早于验证集(无未来数据);
- 目标:log10(reads) − 训练集类目中位数(去类目量级);
- 特征:score_v2.extract_features(标题/正文/类目),无任何结果类字段;
- 拟合:score_v2.fit(IRLS 分位数回归 τ=0.5),默认参数,不改任何口径。

产物:03_intelligence/score_v2_model.json(正式链路 score_v2_apply 唯一读取的模型)
用法:$env:PYTHONPATH='D:\\ttxt'; python scripts/score_v2_fit_model.py
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import datetime
from pathlib import Path

from ttxt.analysis import score_v2

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = ("technology", "finance", "health", "history", "social")
OUT = ROOT / "03_intelligence" / "score_v2_model.json"


def load_rows() -> list[dict]:
    import sqlite3

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

    train_features = [score_v2.extract_features(r["title"], r["content"], r["category"]) for r in train]
    model = score_v2.fit(train_features, [target(r) for r in train])

    meta = {
        "fit_source": "scripts/score_v2_fit_model.py",
        "study_evidence": "03_intelligence/score_v2_study-20261119.json",
        "sample": {
            "rows_with_reads": len(rows),
            "dated": len(parsed),
            "train": len(train),
            "holdout": len(test),
            "train_published_min": min(r["published_at"] for r in train),
            "train_published_max": max(r["published_at"] for r in train),
            "holdout_published_min": min(r["published_at"] for r in test),
            "category_median_log10_reads_train": {k: round(v, 4) for k, v in category_median.items()},
        },
        "target": "log10(reads) - 训练集类目中位数;特征无结果类字段",
    }
    path = score_v2.save_model(model, OUT, meta=meta)
    print(json.dumps({"saved": str(path), "coefficients": model["coefficients_raw"],
                      "intercept": model["intercept_raw"], "n_train": model["n_train"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
