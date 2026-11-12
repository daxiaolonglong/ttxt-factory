"""爆款标签:严格区分"已验证 / 潜力 / 普通 / 低质 / 未评分(缺数据)"。

没有真实曝光与互动数据时,一律标记 unscored + missing_data,禁止臆测。
"""

from __future__ import annotations

import json
from pathlib import Path

from ttxt import config as cfg
from ttxt.db import connect

LABEL_VERIFIED = "verified_viral"
LABEL_POTENTIAL = "potential"
LABEL_NORMAL = "normal"
LABEL_LOW = "low_quality"
LABEL_UNSCORED = "unscored"

TIER_MEASURED = "measured"
TIER_NO_IMPRESSIONS = "no_impressions"
TIER_INCOMPLETE = "incomplete_data"
TIER_MISSING = "missing_data"

# 校准:分位阈值只在足量真实测得样本上拟合;样本不足一律不改口径
MIN_CALIBRATION_SAMPLE = 30
CALIBRATION_META_KEY = "viral_calibration"
DEFAULT_THRESHOLDS = (0.15, 0.08, 0.02)  # verified / potential / normal 下界(设计假定,未校准)


def load_calibration(con) -> dict | None:
    row = con.execute("SELECT value FROM articles_meta WHERE key=?", (CALIBRATION_META_KEY,)).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or "thresholds" not in data:
        return None
    return data


def calibrate(category: str, db_path: Path | None = None) -> dict:
    """从**真实测得**样本拟合分位阈值(verified=p90、potential=p60、normal=p25)。

    样本 < MIN_CALIBRATION_SAMPLE → insufficient_sample,不写入、不改口径。
    只使用 tier=measured 的既有分数(来自授权导入指标),绝不使用合成/估计分数拟合。
    """
    target = Path(db_path) if db_path is not None else cfg.CATEGORIES_DIR / category / "articles.db"
    con = connect(target)
    try:
        scores = sorted(
            row["score"]
            for row in con.execute(
                "SELECT score FROM viral_labels WHERE confidence_tier=? AND score IS NOT NULL",
                (TIER_MEASURED,),
            )
        )
        n = len(scores)
        if n < MIN_CALIBRATION_SAMPLE:
            return {
                "category": category,
                "status": "insufficient_sample",
                "sample": n,
                "required": MIN_CALIBRATION_SAMPLE,
                "message": "真实测得样本不足,保持默认阈值并如实标注未校准",
            }

        def quantile(q: float) -> float:
            idx = min(n - 1, max(0, round(q * (n - 1))))
            return scores[idx]

        verified, potential, normal = quantile(0.90), quantile(0.60), quantile(0.25)
        if not (verified >= potential >= normal):
            return {
                "category": category,
                "status": "degenerate_distribution",
                "sample": n,
                "message": "分位阈值退化(分布异常),保持默认阈值",
            }
        data = {
            "thresholds": [round(verified, 4), round(potential, 4), round(normal, 4)],
            "sample": n,
            "method": "p90/p60/p25 of measured scores",
            "calibrated_at": cfg.now_iso(),
        }
        con.execute(
            "INSERT OR REPLACE INTO articles_meta(key, value) VALUES(?, ?)",
            (CALIBRATION_META_KEY, json.dumps(data, ensure_ascii=False)),
        )
        con.commit()
        return {"category": category, "status": "calibrated", **data}
    finally:
        con.close()


def compute_features(title: str, content: str, text_length: int) -> dict:
    title = title or ""
    return {
        "text_length": text_length or 0,
        "title_length": len(title),
        "has_number": any(ch.isdigit() for ch in title),
        "has_colon": any(ch in title for ch in (":", "：", "|", "｜")),
        "has_question": ("?" in title) or ("？" in title),
    }


def score_with_metrics(
    features: dict,
    metrics: dict,
    thresholds: tuple[float, float, float] = DEFAULT_THRESHOLDS,
    calibrated: bool = False,
) -> tuple[str, str, float | None, str]:
    impressions = metrics.get("impressions")
    if impressions is None or int(impressions) <= 0:
        return LABEL_UNSCORED, TIER_NO_IMPRESSIONS, None, "缺少曝光量,不得计算完整互动率"
    # 缺失 ≠ 0:任一互动字段缺失时不得代入 0 计算
    for field in ("reads", "likes", "favorites", "comments"):
        if metrics.get(field) is None:
            return (
                LABEL_UNSCORED,
                TIER_INCOMPLETE,
                None,
                f"互动指标 {field} 缺失,不得计算互动率(缺失不等于 0)",
            )
    reads = int(metrics["reads"])
    likes = int(metrics["likes"])
    favorites = int(metrics["favorites"])
    comments = int(metrics["comments"])
    read_rate = reads / int(impressions)
    fav_rate = favorites / reads if reads else 0.0
    like_rate = likes / reads if reads else 0.0
    comment_rate = comments / reads if reads else 0.0
    score = 0.4 * read_rate + 0.25 * fav_rate + 0.2 * like_rate + 0.15 * comment_rate
    verified_min, potential_min, normal_min = thresholds
    if score >= verified_min:
        label = LABEL_VERIFIED
    elif score >= potential_min:
        label = LABEL_POTENTIAL
    elif score >= normal_min:
        label = LABEL_NORMAL
    else:
        label = LABEL_LOW
    provenance = "真实曝光与互动数据 + 校准阈值" if calibrated else "真实曝光与互动数据 + 默认阈值(未校准,如实标注)"
    return label, TIER_MEASURED, round(score, 4), provenance


def label_category(category: str, db_path: Path | None = None) -> dict:
    target = Path(db_path) if db_path is not None else cfg.CATEGORIES_DIR / category / "articles.db"
    con = connect(target)
    try:
        rows = con.execute(
            "SELECT id, title, content, cleaned_content, text_length FROM articles ORDER BY id"
        ).fetchall()
        metrics_rows = {row["article_id"]: dict(row) for row in con.execute("SELECT * FROM article_metrics")}
        calibration = load_calibration(con)
        if calibration:
            thresholds = tuple(calibration["thresholds"])
            calibrated = True
        else:
            thresholds, calibrated = DEFAULT_THRESHOLDS, False
        now = cfg.now_iso()
        con.execute("DELETE FROM viral_labels")
        tiers: dict[str, int] = {}
        for row in rows:
            features = compute_features(
                row["title"], row["cleaned_content"] or row["content"], row["text_length"] or 0
            )
            metrics = metrics_rows.get(row["id"])
            if metrics is None:
                label, tier, score, reason = LABEL_UNSCORED, TIER_MISSING, None, "缺少真实表现指标(未接入授权数据)"
            else:
                label, tier, score, reason = score_with_metrics(features, metrics, thresholds, calibrated)
            con.execute(
                """
                INSERT OR REPLACE INTO viral_labels(
                  article_id, label, confidence_tier, score, reason, features_json, labeled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    label,
                    tier,
                    score,
                    reason,
                    json.dumps(features, ensure_ascii=False),
                    now,
                ),
            )
            tiers[tier] = tiers.get(tier, 0) + 1
        con.commit()
    finally:
        con.close()
    calibration_summary = (
        {"status": "calibrated", "sample": calibration["sample"], "thresholds": calibration["thresholds"]}
        if calibrated
        else {"status": "default_thresholds", "sample": 0}
    )
    return {"articles": len(rows), "tiers": tiers, "calibration": calibration_summary}
