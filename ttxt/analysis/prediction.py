"""B2 预测闭环:类目实测基线预测 → 真实结果回填 → 预测准确率知识沉淀。

诚实性约束:
- 没有任何实测样本时拒绝预测(basis 缺失即不编数);
- 预测分只是特征分桶均值基线,不冒充模型置信度;
- outcome 只从 viral_labels 的 measured 层回填,未达标数据不回填;
- 准确率知识资产需最少样本量(默认 5)才写库,样本不足只报告不落库。
"""

from __future__ import annotations

import json
from pathlib import Path

from ttxt import config as cfg
from ttxt.db import connect

from .viral import compute_features

DEFAULT_MIN_SAMPLES = 5


def _target(db_path: Path | None, category: str) -> Path:
    return Path(db_path) if db_path is not None else cfg.category_db_path(category)


def _bucket(features: dict) -> str:
    if features.get("has_question"):
        return "question"
    if features.get("has_number"):
        return "number"
    return "plain"


def _measured_baselines(con) -> dict[str, list[float]]:
    """{bucket: [实测爆款分]},仅取 confidence_tier='measured' 且有分值。"""
    buckets: dict[str, list[float]] = {}
    rows = con.execute(
        "SELECT v.score AS score, a.title AS title, a.content AS content,"
        " COALESCE(a.text_length, LENGTH(a.content)) AS text_length"
        " FROM viral_labels v JOIN articles a ON a.id = v.article_id"
        " WHERE v.confidence_tier='measured' AND v.score IS NOT NULL"
    ).fetchall()
    for row in rows:
        features = compute_features(row["title"] or "", row["content"] or "", int(row["text_length"] or 0))
        buckets.setdefault(_bucket(features), []).append(float(row["score"]))
        buckets.setdefault("all", []).append(float(row["score"]))
    return buckets


def predict_articles(
    category: str,
    db_path: Path | None = None,
    limit: int | None = None,
) -> dict:
    """为**尚未实测**的文章登记基线预测(已有真值的 measured 文章不许预测)。

    P24 根因修正:旧口径 ``WHERE v.article_id IS NULL`` 只挑"完全没有标签行"的文章,
    而生产里采集入口每轮都跑 analyze/relabel ⇒ 每篇都有 ``viral_labels`` 行(没指标的记
    ``missing_data``)⇒ 候选恒为空,``prediction_records`` 永远是 0,闭环空转。
    另外旧代码 ``LIMIT`` 在"是否已有预测记录"之前生效 ⇒ 即便有候选也会每轮重复挑同一批
    (与 P22 指标候选轮转同一类根因),所以把"已有预测"下推到 SQL 反连接,保证逐轮往前推进。
    """
    target = _target(db_path, category)
    recorded = skipped_no_baseline = 0
    con = connect(target)
    try:
        baselines = _measured_baselines(con)
        if not baselines:
            return {
                "category": category,
                "recorded": 0,
                "skipped_no_baseline": 0,
                "reason": "类目内没有任何 measured 实测样本,拒绝凭空预测",
            }
        unmeasured = "(v.article_id IS NULL OR v.confidence_tier <> 'measured' OR v.score IS NULL)"
        already = int(
            con.execute(
                "SELECT COUNT(*) FROM articles a"
                " LEFT JOIN viral_labels v ON v.article_id = a.id"
                " JOIN prediction_records pr ON pr.category = ? AND pr.subject = 'article:' || a.id"
                f" WHERE {unmeasured}",
                (category,),
            ).fetchone()[0]
        )
        sql = (
            "SELECT a.id, a.title, a.content, COALESCE(a.text_length, LENGTH(a.content)) AS text_length"
            " FROM articles a"
            " LEFT JOIN viral_labels v ON v.article_id = a.id"
            " LEFT JOIN prediction_records pr ON pr.category = ? AND pr.subject = 'article:' || a.id"
            f" WHERE pr.id IS NULL AND {unmeasured}"
            " ORDER BY a.id DESC"
        )
        params: list = [category]
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        for row in con.execute(sql, params).fetchall():
            subject = f"article:{row['id']}"
            features = compute_features(row["title"] or "", row["content"] or "", int(row["text_length"] or 0))
            bucket = _bucket(features)
            samples = baselines.get(bucket) or baselines.get("all") or []
            if not samples:
                skipped_no_baseline += 1
                continue
            predicted = round(sum(samples) / len(samples), 4)
            basis = json.dumps(
                {"method": "measured_bucket_mean", "bucket": bucket, "samples": len(samples)},
                ensure_ascii=False,
            )
            con.execute(
                "INSERT INTO prediction_records(category, subject, predicted_score, basis, predicted_at)"
                " VALUES(?,?,?,?,?)",
                (category, subject, predicted, basis, cfg.now_iso()),
            )
            recorded += 1
        con.commit()
        return {
            "category": category,
            "recorded": recorded,
            "already_has_prediction": already,
            "skipped_no_baseline": skipped_no_baseline,
            "baseline_buckets": {key: len(values) for key, values in baselines.items()},
        }
    finally:
        con.close()


def settle_predictions(category: str, db_path: Path | None = None) -> dict:
    """把已产生 measured 实测标签的文章分数回填为 outcome。"""
    target = _target(db_path, category)
    settled = 0
    con = connect(target)
    try:
        rows = con.execute(
            "SELECT id, subject FROM prediction_records"
            " WHERE category=? AND outcome_score IS NULL AND subject LIKE 'article:%'",
            (category,),
        ).fetchall()
        for row in rows:
            article_id = int(row["subject"].split(":", 1)[1])
            label = con.execute(
                "SELECT score, confidence_tier FROM viral_labels WHERE article_id=?", (article_id,)
            ).fetchone()
            if label is None or label["confidence_tier"] != "measured" or label["score"] is None:
                continue
            con.execute(
                "UPDATE prediction_records SET outcome_score=?, outcome_at=?, notes=? WHERE id=?",
                (float(label["score"]), cfg.now_iso(), "实测回填(viral_labels=measured)", row["id"]),
            )
            settled += 1
        con.commit()
        return {"category": category, "settled": settled, "pending": len(rows) - settled}
    finally:
        con.close()


def prediction_report(category: str, db_path: Path | None = None, min_samples: int | None = None) -> dict:
    """统计预测准确率;样本足够时沉淀 knowledge_assets(prediction_accuracy)。"""
    target = _target(db_path, category)
    if min_samples is None:
        min_samples = int(
            (cfg.load_config().get("prediction", {}) or {}).get("min_samples", DEFAULT_MIN_SAMPLES)
        )
    con = connect(target)
    try:
        rows = con.execute(
            "SELECT predicted_score, outcome_score FROM prediction_records"
            " WHERE category=? AND outcome_score IS NOT NULL",
            (category,),
        ).fetchall()
        n = len(rows)
        report = {"category": category, "settled_samples": n, "mae": None, "mean_bias": None, "asset_written": False}
        if n == 0:
            return report
        errors = [float(row["predicted_score"]) - float(row["outcome_score"]) for row in rows]
        mae = round(sum(abs(err) for err in errors) / n, 4)
        bias = round(sum(errors) / n, 4)
        report["mae"] = mae
        report["mean_bias"] = bias
        if n < min_samples:
            report["note"] = f"样本 {n} < 最少 {min_samples},不写知识资产(样本不足不下结论)"
            return report
        statement = (
            f"类目 {category} 预测校准:已回填 {n} 条,平均绝对误差 {mae:.3f},"
            f"平均偏差 {bias:+.3f}(正=系统性高估)。"
        )
        evidence = json.dumps({"samples": n, "mae": mae, "mean_bias": bias}, ensure_ascii=False)
        con.execute(
            "DELETE FROM knowledge_assets WHERE category=? AND kind='prediction_accuracy'", (category,)
        )
        con.execute(
            "INSERT INTO knowledge_assets(category, kind, statement, evidence_json, support_count, confidence, updated_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (category, "prediction_accuracy", statement, evidence, n, "measured", cfg.now_iso()),
        )
        con.commit()
        report["asset_written"] = True
        return report
    finally:
        con.close()
