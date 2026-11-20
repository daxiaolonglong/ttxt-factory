"""R60-A:用真实 T+3 指标回头验 score_v2 的预测力(**只读**,不改任何东西)。

口径(与 R47/R51 完全一致,公式直接 import ``scripts/score_v2_production_compare.py``,不复制不重写):
- 实际结果:文章 T+3 窗口内实测 ``reads`` 的 ``log10(reads)`` 在**类目内**的名次分位 ``(rank−0.5)/n``;
- lift:按 ``score_v2`` 取类目 top-decile,其实测分位均值 / 0.5(0.5 = 无 lift);
- Spearman ρ + 200 次 percentile bootstrap 95% CI(seed 固定,n<30 不出 CI);
- 样本:**T+3 窗口内取到读数**的文章 = ``performance_windows(subject_type='article',
  window_days=3, status='on_time', reads>0)``。``late/early/overdue/unavailable`` 一律不算
  (口径不同,不与 on_time 混用);再要求该文章在 ``score_v2_labels`` 里有分,且 URL 主机在
  公开指标白名单(``metrics_pages.host_allowed``,即"头条域")内 —— 白名单外拿不到 T+3 读数,
  计进样本只会稀释;
- 泄漏记账:score_v2 模型的拟合截止时间(读 R47 产物里的 ``holdout_published_min``)之后的文章
  才是模型没见过的样本,之前的是样本内。两个口径都出,不混。

样本量算式(与 R58 同一式,别换):
    n ≈ (z(1−α/2) + z(1−β)) / ρ )²,α=0.05(双侧)、power=0.80 ⇒ z 和 = 2.8016
反推当前 n 能检出的最小 ρ:ρ_min = 2.8016 / √n。

用法:$env:PYTHONPATH='D:\\ttxt'; .venv\\Scripts\\python.exe -X utf8 scripts\\r60_score_v2_t3_eval.py
产物:03_intelligence/r60-score-v2-t3-eval-<YYYYMMDD>.json(只读库、只写这个新产物)
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# 公式直接复用 R47 评测脚本,保证"口径别换":(rank−0.5)/n、top-decile lift、Spearman+bootstrap CI
from score_v2_production_compare import (  # noqa: E402
    actual_percentiles,
    bootstrap_ci,
    spearman,
    top_decile,
)

from ttxt import config as cfg  # noqa: E402
from ttxt.crawler import metrics_pages  # noqa: E402

Z_SUM = 1.959963985 + 0.841621234  # α=0.05 双侧 + power 0.80 ⇒ 2.8016(R58 用的就是这个式子)
RHO_TARGETS = (0.10, 0.15, 0.20, 0.30)


def ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def has_table(con: sqlite3.Connection, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def as_ts(value: str):
    """把两种时间戳(DB 原文与 R47 产物)统一成可比对的本地 naive datetime;解不开返回 None。"""
    raw = str(value or "").strip().replace(" ", "T")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def pairs_for(category: str, window_days: int) -> dict:
    """该类目 T+3 头条域可评测对:(score_v2, log10(reads)) + 漏斗计数。"""
    db = cfg.CATEGORIES_DIR / category / "articles.db"
    funnel = {"category": category, "db_exists": db.exists()}
    if not db.exists():
        funnel["pairs"] = []
        return funnel
    con = ro(db)
    try:
        for table in ("performance_windows", "score_v2_labels", "articles"):
            funnel[f"has_{table}"] = has_table(con, table)
        if not (funnel.get("has_performance_windows") and funnel.get("has_score_v2_labels")):
            funnel["pairs"] = []
            return funnel
        funnel["window_rows"] = {
            f"{row['status']}": int(row["n"])
            for row in con.execute(
                "SELECT status, COUNT(*) AS n FROM performance_windows"
                " WHERE subject_type='article' AND window_days=? GROUP BY status",
                (window_days,),
            )
        }
        funnel["on_time_with_reads"] = int(
            con.execute(
                "SELECT COUNT(*) FROM performance_windows WHERE subject_type='article'"
                " AND window_days=? AND status='on_time' AND reads IS NOT NULL AND reads>0",
                (window_days,),
            ).fetchone()[0]
        )
        rows = con.execute(
            "SELECT CAST(w.subject_key AS INTEGER) AS article_id, w.reads AS reads, w.captured_at AS captured_at,"
            " a.published_at AS published_at, a.url AS url, a.url_normalized AS url_normalized,"
            " s.score_v2 AS score_v2"
            " FROM performance_windows w"
            " JOIN articles a ON a.id = CAST(w.subject_key AS INTEGER)"
            " JOIN score_v2_labels s ON s.article_id = CAST(w.subject_key AS INTEGER)"
            " WHERE w.subject_type='article' AND w.window_days=? AND w.status='on_time'"
            " AND w.reads IS NOT NULL AND w.reads>0",
            (window_days,),
        ).fetchall()
        funnel["scored_on_time"] = len(rows)
        pairs = []
        day_hist: dict[str, int] = {}
        for row in rows:
            host_ok = metrics_pages.host_allowed(str(row["url_normalized"] or row["url"] or ""))
            if not host_ok:
                continue
            pairs.append(
                {
                    "article_id": int(row["article_id"]),
                    "score_v2": float(row["score_v2"]),
                    "log_reads": math.log10(int(row["reads"])),
                    "published_at": str(row["published_at"] or ""),
                    "published_ts": as_ts(row["published_at"]),
                    "captured_at": str(row["captured_at"] or ""),
                }
            )
            stamp = as_ts(row["captured_at"])
            if stamp is not None:
                key = stamp.date().isoformat()
                day_hist[key] = day_hist.get(key, 0) + 1
        funnel["whitelist_pairs"] = len(pairs)
        funnel["captured_day_hist"] = dict(sorted(day_hist.items()))
        funnel["pairs"] = pairs
        return funnel
    finally:
        con.close()


def block_for(pairs: list[dict]) -> dict:
    """一组对的全部读数(公式与 R47 逐字相同)。"""
    out: dict = {"n": len(pairs)}
    if not pairs:
        return out
    pcts = actual_percentiles([p["log_reads"] for p in pairs])
    scores = [p["score_v2"] for p in pairs]
    rho = spearman(scores, pcts)
    out["distinct_score_values"] = len(set(scores))
    out["spearman_vs_actual_pct"] = None if rho is None else round(rho, 4)
    out["spearman_ci95"] = bootstrap_ci(scores, pcts)
    out["top_decile"] = top_decile(list(zip(scores, pcts)))
    out["median_actual_reads"] = int(
        sorted(10 ** p["log_reads"] for p in pairs)[len(pairs) // 2]
    )
    return out


def sample_size_table(n_now: int, pairs_per_day: float) -> dict:
    rows = []
    for rho in RHO_TARGETS:
        need = math.ceil((Z_SUM / rho) ** 2)
        rows.append(
            {
                "rho": rho,
                "n_needed": need,
                "gap_pairs": max(0, need - n_now),
                "days_at_observed_rate": (
                    None if pairs_per_day <= 0 else round((need - n_now) / pairs_per_day, 1) if need > n_now else 0.0
                ),
            }
        )
    return {
        "formula": "n ≈ ((z(1−α/2)+z(1−β))/ρ)²,α=0.05 双侧、power=0.80 ⇒ z 和=2.8016(R58 同式)",
        "z_sum": round(Z_SUM, 4),
        "smallest_detectable_rho_at_current_n": (
            None if n_now <= 3 else round(Z_SUM / math.sqrt(n_now), 4)
        ),
        "current_n": n_now,
        "targets": rows,
    }


def main() -> None:
    from ttxt.writing import publication

    window_days = publication.window_days()
    boundary_path = ROOT / "03_intelligence" / "score_v2_production_compare-20261119.json"
    boundary_raw = None
    if boundary_path.exists():
        try:
            boundary_raw = json.loads(boundary_path.read_text(encoding="utf-8"))["time_split"]["holdout_published_min"]
        except (OSError, ValueError, KeyError):
            boundary_raw = None
    boundary = as_ts(boundary_raw)

    per_category: dict = {}
    pooled: list[dict] = []
    day_hist: dict[str, int] = {}
    for category in list(cfg.load_config().get("categories", [])):
        funnel = pairs_for(category, window_days)
        pairs = funnel.pop("pairs")
        for day, count in funnel.pop("captured_day_hist", {}).items():
            day_hist[day] = day_hist.get(day, 0) + count
        per_category[category] = {**funnel, "metrics": block_for(pairs)}
        pooled.extend(pairs)

    in_sample = [p for p in pooled if boundary and p["published_ts"] and p["published_ts"] < boundary]
    out_of_sample = [p for p in pooled if not (boundary and p["published_ts"]) or p["published_ts"] >= boundary]

    # 增长按"观测日历"折算:样本不是匀速长出来的,补采还会改写窗口行,captured_at 的日历跨度
    # 只是可核对的上界,不是稳态日增 —— 别把"最近 N 天全量"当成日增。
    span_days = 0
    if day_hist:
        days = sorted(day_hist)
        span_days = (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days + 1
    rate = len(pooled) / span_days if span_days else 0.0
    table = sample_size_table(len(pooled), rate)
    table["observation_growth"] = {
        "captured_day_hist": day_hist,
        "observed_days": len(day_hist),
        "span_days": span_days,
        "pairs_per_day": round(rate, 2),
        "caveat": "captured_at 是窗口内观测时刻(补采会改写),按观测日历折算是可核对的上界,不是稳态日增",
    }

    report = {
        "schema": "r60-score-v2-t3-eval-v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "window_days": window_days,
        "grace_hours": publication.WINDOW_GRACE_HOURS,
        "sample_rule": (
            "performance_windows(subject_type='article', window_days=%d, status='on_time', reads>0)"
            " ∩ score_v2_labels 有分 ∩ URL 主机在公开指标白名单(头条域:www.toutiao.com / m.toutiao.com);"
            "late/early/overdue/unavailable/pending 一律不计(口径不同)" % window_days
        ),
        "metric_semantics": {
            "actual": "T+3 实测 log10(reads) 的**类目内**名次分位 (rank−0.5)/n(均值恰为 0.5)",
            "lift": "按 score_v2 取类目 top-decile,其实测分位均值 / 0.5(1.0=无 lift)",
            "formula_source": "scripts/score_v2_production_compare.py(import,不复制)",
        },
        "leakage_note": {
            "model_holdout_boundary_published_min": boundary_raw,
            "in_sample_n": len(in_sample),
            "out_of_sample_n": len(out_of_sample),
            "meaning": "boundary 之前发布 ⇒ 拟合时见过(样本内);之后 ⇒ 模型没见过",
        },
        "categories": per_category,
        "pooled": block_for(pooled),
        "by_leakage": {
            "in_sample": block_for(in_sample),
            "out_of_sample": block_for(out_of_sample),
        },
        "sample_size": table,
    }
    out = ROOT / "03_intelligence" / f"r60-score-v2-t3-eval-{datetime.now().strftime('%Y%m%d')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {out}")
    print(json.dumps({"window_days": window_days, "boundary": boundary_raw}, ensure_ascii=False))
    for slug, block in per_category.items():
        m = block["metrics"]
        print(
            slug,
            json.dumps(
                {
                    "window_rows": block.get("window_rows"),
                    "on_time_with_reads": block.get("on_time_with_reads"),
                    "scored": block.get("scored_on_time"),
                    "whitelist_pairs": block.get("whitelist_pairs"),
                    "n": m.get("n"),
                    "rho": m.get("spearman_vs_actual_pct"),
                    "ci95": m.get("spearman_ci95"),
                    "lift": (m.get("top_decile") or {}).get("lift"),
                },
                ensure_ascii=False,
            ),
        )
    print("POOLED", json.dumps(report["pooled"], ensure_ascii=False))
    print("BY_LEAKAGE", json.dumps(report["by_leakage"], ensure_ascii=False))
    print("SAMPLE_SIZE", json.dumps(report["sample_size"], ensure_ascii=False))


if __name__ == "__main__":
    main()
