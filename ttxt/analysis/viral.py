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
TIER_MISSING = "missing_data"


def compute_features(title: str, content: str, text_length: int) -> dict:
    title = title or ""
    return {
        "text_length": text_length or 0,
        "title_length": len(title),
        "has_number": any(ch.isdigit() for ch in title),
        "has_colon": any(ch in title for ch in (":", "：", "|", "｜")),
        "has_question": ("?" in title) or ("？" in title),
    }


def score_with_metrics(features: dict, metrics: dict) -> tuple[str, str, float | None, str]:
    impressions = metrics.get("impressions")
    if not impressions or int(impressions) <= 0:
        return LABEL_UNSCORED, TIER_NO_IMPRESSIONS, None, "缺少曝光量,不得计算完整互动率"
    reads = int(metrics.get("reads") or 0)
    likes = int(metrics.get("likes") or 0)
    favorites = int(metrics.get("favorites") or 0)
    comments = int(metrics.get("comments") or 0)
    read_rate = reads / int(impressions)
    fav_rate = favorites / reads if reads else 0.0
    like_rate = likes / reads if reads else 0.0
    comment_rate = comments / reads if reads else 0.0
    score = 0.4 * read_rate + 0.25 * fav_rate + 0.2 * like_rate + 0.15 * comment_rate
    if score >= 0.15:
        label = LABEL_VERIFIED
    elif score >= 0.08:
        label = LABEL_POTENTIAL
    elif score >= 0.02:
        label = LABEL_NORMAL
    else:
        label = LABEL_LOW
    return label, TIER_MEASURED, round(score, 4), "基于真实曝光与互动数据"


def label_category(category: str, db_path: Path | None = None) -> dict:
    target = Path(db_path) if db_path is not None else cfg.CATEGORIES_DIR / category / "articles.db"
    con = connect(target)
    try:
        rows = con.execute(
            "SELECT id, title, content, cleaned_content, text_length FROM articles ORDER BY id"
        ).fetchall()
        metrics_rows = {row["article_id"]: dict(row) for row in con.execute("SELECT * FROM article_metrics")}
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
                label, tier, score, reason = score_with_metrics(features, metrics)
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
    return {"articles": len(rows), "tiers": tiers}
