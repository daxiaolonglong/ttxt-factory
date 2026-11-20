"""R60 G3:评分区分度诊断报告(不改评分、不动门禁,只把真实分布交出来)。

出处:08_wiki/reports/r58-capacity-baseline.md §6——"下一轮先处理评分标定/区分度
(score_v2>=0.5 仅 3/654)"。本脚本回答三个问题:

1. 分位档(viral 的 p90/p60/p25 标定阈值)下的标签分布是什么?
   ⇒ 区分度看的是**档位占比**,不是 score_v2 绝对分过 0.5 的个数。
2. score_v2 连续分的真实分布(分位数)与和实测表现的可区分性(样本对数)。
3. 各有多少文章因"无指标"被挡在样本外(missing_data 档)——
   r58 提到的 technology 423 篇白名单外文章在这层的真实口径。

输出:03_intelligence/metrics_public/r60-score-dist-<stamp>.json(已 gitignore,
纯本机取证)+ stdout 人类可读摘要。只读各库,零写入。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ttxt import config as cfg
from ttxt.analysis import score_v2, viral
from ttxt.db import connect

CATEGORIES = ["technology", "finance", "social", "history", "health"]


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(ordered) - 1)
    frac = pos - lo
    return round(ordered[lo] * (1 - frac) + ordered[hi] * frac, 4)


def probe(category: str) -> dict:
    path = cfg.CATEGORIES_DIR / category / "articles.db"
    con = connect(path)
    try:
        calibration = viral.load_calibration(con)
        calibration_reads = viral.load_calibration_reads(con)
        label_counts: dict[str, int] = {}
        tier_counts: dict[str, int] = {}
        scores_measured: list[float] = []
        for row in con.execute(
            "SELECT label, confidence_tier, score FROM viral_labels"
        ):
            label_counts[row["label"]] = label_counts.get(row["label"], 0) + 1
            tier_counts[row["confidence_tier"]] = tier_counts.get(row["confidence_tier"], 0) + 1
            if row["confidence_tier"] in (viral.TIER_MEASURED, viral.TIER_MEASURED_READS) and row["score"] is not None:
                scores_measured.append(float(row["score"]))
        total_labels = sum(label_counts.values())
        scored_pairs = sum(
            1 for row in con.execute(
                "SELECT score FROM viral_labels WHERE confidence_tier IN (?, ?) AND score IS NOT NULL",
                (viral.TIER_MEASURED, viral.TIER_MEASURED_READS),
            )
        )
        # score_v2:模型存在时,对全部有标签文章跑连续分,看分布(不重新拟合)。
        v2_stats: dict = {"model": "absent"}
        model_path = cfg.INTELLIGENCE_DIR / "score_v2_model.json"
        if model_path.exists():
            try:
                model = score_v2.load_model(model_path)
                values: list[float] = []
                for row in con.execute(
                    "SELECT a.title, COALESCE(a.cleaned_content, a.content, '') AS body"
                    " FROM viral_labels v JOIN articles a ON a.id = v.article_id"
                ):
                    features = score_v2.extract_features(row["title"] or "", row["body"] or "", category)
                    values.append(float(score_v2.predict(model, features)))
                v2_stats = {
                    "model": "loaded",
                    "tau": model.get("tau"),
                    "n": len(values),
                    "p25": _quantile(values, 0.25),
                    "p50": _quantile(values, 0.50),
                    "p75": _quantile(values, 0.75),
                    "p90": _quantile(values, 0.90),
                    "share_above_0_5": round(
                        sum(1 for v in values if v >= 0.5) / len(values), 4) if values else None,
                }
            except Exception as exc:  # noqa: BLE001 - 诊断脚本:读不动就如实记录
                v2_stats = {"model": "load_failed", "error": str(exc)[:200]}
        return {
            "labels": label_counts,
            "tiers": tier_counts,
            "total_label_rows": total_labels,
            "measured_score_pairs": scored_pairs,
            "calibration": calibration or "uncalibrated(exposure_pool)",
            "calibration_reads": calibration_reads or "uncalibrated(reads_pool)",
            "measured_score_quantiles": {
                "p25": _quantile(scores_measured, 0.25),
                "p50": _quantile(scores_measured, 0.50),
                "p75": _quantile(scores_measured, 0.75),
                "p90": _quantile(scores_measured, 0.90),
            },
            "score_v2": v2_stats,
            "band_share(verified+potential / scored)": (
                round((label_counts.get(viral.LABEL_VERIFIED, 0)
                       + label_counts.get(viral.LABEL_POTENTIAL, 0)) / scored_pairs, 4)
                if scored_pairs else None
            ),
        }
    finally:
        con.close()


def main() -> int:
    report = {
        "generated_at": cfg.now_iso(),
        "purpose": "R60 G3 区分度诊断:档位占比替代 score_v2 绝对阈值",
        "note": "区分度看 band 占比(标定即保证 verified≈p90≈10%),"
                "score_v2 是连续排序分,过 0.5 的个数不是质量指标(r58 §6 的 3/654 属口径误读)",
        "categories": {c: probe(c) for c in CATEGORIES},
    }
    out_dir = cfg.INTELLIGENCE_DIR / "metrics_public"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = cfg.now_iso().replace(":", "").replace("-", "").replace("+0800", "")
    out_path = out_dir / f"r60-score-dist-{stamp}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for category, data in report["categories"].items():
        print(f"[{category}] 标签行={data['total_label_rows']} 真实测得对={data['measured_score_pairs']}"
              f" band占比={data['band_share(verified+potential / scored)']}"
              f" missing_data={data['tiers'].get(viral.TIER_MISSING, 0)}"
              f" v2={data['score_v2'].get('model')}"
              f" p50={data['measured_score_quantiles']['p50']}")
    print(f"报告已写入: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
