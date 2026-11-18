"""爆款标签:严格区分"已验证 / 潜力 / 普通 / 低质 / 未评分(缺数据)"。

没有真实曝光与互动数据时,一律标记 unscored + missing_data,禁止臆测。
有曝光但没有收藏数时(公开页结构上拿不到收藏),只在可观测的率上按同一权重归一化,
绝不把缺失的收藏数代入 0,并把"用了哪些字段"写进 features_json / reason。
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
# T-25:有阅读量但没有曝光量的样本单独成池,用阅读量做分母重新计分。
# 与 measured **严格分池**:不共用 p90/p60/p25 阈值、不共用 meta key、不互相补样本。
TIER_MEASURED_READS = "measured_reads"
TIER_NO_IMPRESSIONS = "no_impressions"
TIER_INCOMPLETE = "incomplete_data"
TIER_MISSING = "missing_data"

# 校准:分位阈值只在足量真实测得样本上拟合;样本不足一律不改口径
MIN_CALIBRATION_SAMPLE = 30
CALIBRATION_META_KEY = "viral_calibration"
READS_CALIBRATION_META_KEY = "viral_calibration_reads"
DEFAULT_THRESHOLDS = (0.15, 0.08, 0.02)  # verified / potential / normal 下界(设计假定,未校准)
# reads 池的默认下界也是设计假定(未校准),只在样本不足 30 时临时使用。
# 该池的分数是**阅读量单调映射**(reads/(reads+READS_SCALE)),不是曝光率/互动率,
# 因此默认值按"阅读量量级"给(约 1000 阅读≈0.5、250≈0.2、53≈0.05),与 measured 池不是同一把尺子。
DEFAULT_THRESHOLDS_READS = (0.5, 0.2, 0.05)
# 阅读量口径的尺度常数:把原始阅读量压到 (0,1) 且保持单调,仅用于排序与分位标定。
READS_SCALE = 1000.0

METRIC_FIELD_ORDER = ("reads", "likes", "favorites", "comments", "impressions")
# 核心互动字段:任一个缺失 ⇒ 无法计算互动率(incomplete_data)。
# favorites 不在其中:头条公开页结构上拿不到收藏数,只有后台导出(verified/self_reported)有;
# 缺失时按可观测率归一化,不代入 0(缺失 ≠ 0,规则 6)。
CORE_ENGAGEMENT_FIELDS = ("reads", "likes", "comments")
WEIGHT_READ = 0.40
WEIGHT_FAVORITE = 0.25
WEIGHT_LIKE = 0.20
WEIGHT_COMMENT = 0.15


def _load_calibration(con, meta_key: str) -> dict | None:
    row = con.execute("SELECT value FROM articles_meta WHERE key=?", (meta_key,)).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or "thresholds" not in data:
        return None
    return data


def load_calibration(con) -> dict | None:
    """measured 池的阈值(曝光率口径)。"""
    return _load_calibration(con, CALIBRATION_META_KEY)


def load_calibration_reads(con) -> dict | None:
    """measured_reads 池的阈值(阅读量口径)。与 measured 池互不读取。"""
    return _load_calibration(con, READS_CALIBRATION_META_KEY)


def _calibrate_pool(
    category: str,
    db_path: Path | None,
    *,
    tier: str,
    meta_key: str,
    method: str,
    insufficient_message: str,
) -> dict:
    target = Path(db_path) if db_path is not None else cfg.CATEGORIES_DIR / category / "articles.db"
    con = connect(target)
    try:
        scores = sorted(
            row["score"]
            for row in con.execute(
                "SELECT score FROM viral_labels WHERE confidence_tier=? AND score IS NOT NULL",
                (tier,),
            )
        )
        n = len(scores)
        if n < MIN_CALIBRATION_SAMPLE:
            return {
                "category": category,
                "status": "insufficient_sample",
                "sample": n,
                "required": MIN_CALIBRATION_SAMPLE,
                "message": insufficient_message,
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
            "method": method,
            "calibrated_at": cfg.now_iso(),
        }
        con.execute(
            "INSERT OR REPLACE INTO articles_meta(key, value) VALUES(?, ?)",
            (meta_key, json.dumps(data, ensure_ascii=False)),
        )
        con.commit()
        return {"category": category, "status": "calibrated", **data}
    finally:
        con.close()


def calibrate(category: str, db_path: Path | None = None) -> dict:
    """从**曝光率口径**的真实测得样本拟合分位阈值(verified=p90、potential=p60、normal=p25)。

    样本 < MIN_CALIBRATION_SAMPLE → insufficient_sample,不写入、不改口径。
    只使用 tier=measured 的既有分数(来自授权导入指标),绝不使用合成/估计分数拟合,
    也绝不把 measured_reads 的分数混进来。
    """
    return _calibrate_pool(
        category,
        db_path,
        tier=TIER_MEASURED,
        meta_key=CALIBRATION_META_KEY,
        method="p90/p60/p25 of measured scores",
        insufficient_message="真实测得样本不足,保持默认阈值并如实标注未校准",
    )


def calibrate_reads(category: str, db_path: Path | None = None) -> dict:
    """从**阅读量口径**的真实测得样本单独拟合阈值,与 measured 池严格分池。

    只使用 tier=measured_reads(有阅读量、无曝光量)的既有分数;样本不足同样如实拒绝。
    """
    return _calibrate_pool(
        category,
        db_path,
        tier=TIER_MEASURED_READS,
        meta_key=READS_CALIBRATION_META_KEY,
        method="p90/p60/p25 of measured_reads scores (reads-based, independent pool)",
        insufficient_message="阅读量口径的真实样本不足,保持该池默认阈值并如实标注未校准",
    )


def compute_features(title: str, content: str, text_length: int) -> dict:
    title = title or ""
    return {
        "text_length": text_length or 0,
        "title_length": len(title),
        "has_number": any(ch.isdigit() for ch in title),
        "has_colon": any(ch in title for ch in (":", "：", "|", "｜")),
        "has_question": ("?" in title) or ("？" in title),
    }


def _label_for_score(score: float, thresholds: tuple[float, float, float]) -> str:
    verified_min, potential_min, normal_min = thresholds
    if score >= verified_min:
        return LABEL_VERIFIED
    if score >= potential_min:
        return LABEL_POTENTIAL
    if score >= normal_min:
        return LABEL_NORMAL
    return LABEL_LOW


def _weighted_score(rates: list[tuple[float, float]]) -> float:
    return sum(weight * rate for weight, rate in rates) / sum(weight for weight, _rate in rates)


def score_with_metrics(
    features: dict,
    metrics: dict,
    thresholds: tuple[float, float, float] = DEFAULT_THRESHOLDS,
    calibrated: bool = False,
    reads_thresholds: tuple[float, float, float] = DEFAULT_THRESHOLDS_READS,
    reads_calibrated: bool = False,
) -> tuple[str, str, float | None, str]:
    """两条互不混池的计分路径。

    - 有曝光量:``measured``,以曝光率为基准(原口径);
    - 无曝光量但有阅读量:``measured_reads``,以阅读量为基准,阈值用独立的一套;
    - 两者都缺:``no_impressions``,不评分、不猜。
    """
    impressions = metrics.get("impressions")
    if impressions is not None and int(impressions) > 0:
        # 缺失 ≠ 0:核心互动字段(阅读/点赞/评论)缺失时不得代入 0 计算
        for field in CORE_ENGAGEMENT_FIELDS:
            if metrics.get(field) is None:
                return (
                    LABEL_UNSCORED,
                    TIER_INCOMPLETE,
                    None,
                    f"互动指标 {field} 缺失,不得计算互动率(缺失不等于 0)",
                )
        reads = int(metrics["reads"])
        # 加权评分:阅读率 40% / 收藏率 25% / 点赞率 20% / 评论率 15%(职责口径)。
        # P22:收藏数公开页结构上不可得(只有后台导出有);缺失时**不代入 0**,
        # 只在可观测的率上按同一权重归一化,并在 reason 里如实标注口径。
        rates: list[tuple[float, float]] = [
            (WEIGHT_READ, reads / int(impressions)),
            (WEIGHT_LIKE, int(metrics["likes"]) / reads if reads else 0.0),
            (WEIGHT_COMMENT, int(metrics["comments"]) / reads if reads else 0.0),
        ]
        partial = metrics.get("favorites") is None
        if not partial:
            rates.insert(1, (WEIGHT_FAVORITE, int(metrics["favorites"]) / reads if reads else 0.0))
        score = _weighted_score(rates)
        label = _label_for_score(score, thresholds)
        provenance = "真实曝光与互动数据 + 校准阈值" if calibrated else "真实曝光与互动数据 + 默认阈值(未校准,如实标注)"
        if partial:
            provenance += ";收藏指标该来源不可得,按可观测率归一化(未代入 0)"
        return label, TIER_MEASURED, round(score, 4), provenance

    # T-25:无曝光量。有阅读量就单独成池,按**阅读量**计分;连阅读量都没有才如实不评分。
    # 这两个通道的公开页只稳定给出 readCount,点赞/评论常常没有 ⇒ 不能要求它们,
    # 也不能把缺失的点赞/评论/收藏代入 0 去算"互动率"。因此该池只做阅读量单调映射,
    # 标签含义是"类目内阅读量分位",不是曝光率或互动率。
    reads = metrics.get("reads")
    if reads is None:
        return LABEL_UNSCORED, TIER_NO_IMPRESSIONS, None, "缺少曝光量,不得计算完整互动率"
    reads = int(reads)
    if reads <= 0:
        return LABEL_UNSCORED, TIER_NO_IMPRESSIONS, None, "缺少曝光量且阅读量为 0,不评分"
    score = reads / (reads + READS_SCALE)
    label = _label_for_score(score, reads_thresholds)
    provenance = (
        "真实阅读量(无曝光量,独立口径:仅按阅读量排序) + 校准阈值"
        if reads_calibrated
        else "真实阅读量(无曝光量,独立口径:仅按阅读量排序) + 默认阈值(未校准,如实标注)"
    )
    provenance += ";曝光量缺失,不以曝光率计分,不与 measured 池共用阈值"
    provenance += ";点赞/评论/收藏不参与该口径,缺失不代入 0"
    return label, TIER_MEASURED_READS, round(score, 4), provenance


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
        reads_calibration = load_calibration_reads(con)
        if reads_calibration:
            reads_thresholds = tuple(reads_calibration["thresholds"])
            reads_calibrated = True
        else:
            reads_thresholds, reads_calibrated = DEFAULT_THRESHOLDS_READS, False
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
                # 打分口径留痕:这一条分数是由哪几个真实字段算出来的(缺失的字段不出现)
                features["metric_fields"] = [
                    field for field in METRIC_FIELD_ORDER if metrics.get(field) is not None
                ]
                label, tier, score, reason = score_with_metrics(
                    features,
                    metrics,
                    thresholds,
                    calibrated,
                    reads_thresholds,
                    reads_calibrated,
                )
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
    reads_calibration_summary = (
        {
            "status": "calibrated",
            "sample": reads_calibration["sample"],
            "thresholds": reads_calibration["thresholds"],
        }
        if reads_calibrated
        else {"status": "default_thresholds", "sample": 0}
    )
    return {
        "articles": len(rows),
        "tiers": tiers,
        "calibration": calibration_summary,
        "calibration_reads": reads_calibration_summary,
    }
