"""R43:用已采集的真实阅读量当教材,做可解释的特征统计(只读,不改任何评分/门禁/取样)。

口径:
- 目标变量 = 采集时点的 ``reads``(整数),取 ``log10``;heavy-tail 不用原始均值。
- 读数不是同一时刻的:**先按 (类目, 发布后 12 小时龄段) 中位数去偏**,得到 ``adj_log``,
  所有特征效应都报在 ``adj_log`` 上,原始的也一并给出。
- 分档:类目内 reads 分位 ≥90% 为「爆款档」,<50% 为「低阅读档」。
- 统计:Spearman(带 Fisher z 95%CI)、Welch 均值差 95%CI、Wilson/Newcombe 比例差 95%CI。
- 样本 <30 的分组一律标 ``insufficient``,不参与方向稳定性的判定。
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\ttxt")
CATS = ["technology", "finance", "social", "history", "health"]
OUT = ROOT / "03_intelligence" / "score_feature_study-20261119.json"

CONTRAST_WORDS = ("对比", "相比", "却", "但", "然而", "不是", "而是", "还是", "反而", "竟然", "不如", "vs", "VS", "对比")
EMOTION_WORDS = ("震惊", "重磅", "突发", "刚刚", "暴涨", "暴跌", "炸", "疯", "崩", "罕见", "意外",
                 "终于", "警告", "危机", "豪赌", "血洗", "吓人", "可怕", "逆袭", "翻盘", "内幕", "真相")


# ------------------------------------------------------------------ 基础统计
def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def var(xs):
    if len(xs) < 2:
        return float("nan")
    m = mean(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return float("nan")
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2


def quantile(xs, q):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    idx = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return xs[idx]


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def spearman(x, y):
    n = len(x)
    if n < 4:
        return {"rho": None, "ci": None, "n": n}
    rx, ry = ranks(x), ranks(y)
    mx, my = mean(rx), mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    rho = num / den if den else 0.0
    rho = max(-0.999999, min(0.999999, rho))
    z = math.atanh(rho)
    se = 1 / math.sqrt(n - 3)
    return {
        "rho": round(rho, 4),
        "ci95": [round(math.tanh(z - 1.96 * se), 4), round(math.tanh(z + 1.96 * se), 4)],
        "n": n,
    }


def welch_ci(a, b):
    """mean(a)-mean(b) 的 95%CI(正态近似,大样本)。"""
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = mean(a), mean(b)
    se = math.sqrt(var(a) / len(a) + var(b) / len(b))
    d = ma - mb
    return {"diff": d, "lo": d - 1.96 * se, "hi": d + 1.96 * se, "se": se}


def wilson(k, n):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    z = 1.96
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def newcombe_diff(k1, n1, k0, n0):
    """比例差 p1-p0 的 Newcombe hybrid score 95%CI。"""
    if n1 == 0 or n0 == 0:
        return None
    p1, p0 = k1 / n1, k0 / n0
    l1, u1 = wilson(k1, n1)
    l0, u0 = wilson(k0, n0)
    d = p1 - p0
    lo = d - math.sqrt((p1 - l1) ** 2 + (u0 - p0) ** 2)
    hi = d + math.sqrt((u1 - p1) ** 2 + (p0 - l0) ** 2)
    return {"diff": d, "lo": lo, "hi": hi, "p1": p1, "p0": p0, "n1": n1, "n0": n0}


# ------------------------------------------------------------------ 取数
def parse_dt(text):
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt


def source_type(src: str) -> str:
    s = (src or "").lower()
    if s.startswith("toutiao"):
        return "toutiao"
    if s.endswith("_rss") or s.endswith("_feed") or s.endswith("_atom"):
        return "rss"
    if "legacy" in s:
        return "aggregator"
    return "other"


def split_title(title):
    t = (title or "").strip()
    return t


def body_stats(content, text_length):
    raw = content or ""
    if "<p" in raw.lower():
        paras = [p for p in re.split(r"<p[^>]*>", raw, flags=re.I)]
        paras = [re.sub(r"<[^>]+>", "", p).strip() for p in paras]
    else:
        paras = [p.strip() for p in raw.split("\n")]
    paras = [p for p in paras if len(p) >= 10]
    body_chars = int(text_length or 0) or len(re.sub(r"\s+", "", raw))
    avg_para = body_chars / len(paras) if paras else 0.0
    return len(paras), avg_para, bool(re.search(r"<strong|<h2|<h3", raw, re.I))


def has_image(content):
    raw = (content or "").lower()
    return ("<img" in raw) or ("![" in raw)


def load_rows():
    rows = []
    for cat in CATS:
        path = ROOT / "01_categories" / cat / "articles.db"
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        sql = """
        SELECT a.id, a.category, a.source, a.title, a.content, a.text_length,
               a.published_at, m.reads, m.captured_at, m.impressions, m.data_source
        FROM articles a JOIN article_metrics m ON m.article_id = a.id
        WHERE m.reads IS NOT NULL AND m.reads > 0
        """
        for r in con.execute(sql):
            d = dict(r)
            pub = parse_dt(d["published_at"])
            cap = parse_dt(d["captured_at"])
            if pub is None or cap is None:
                continue
            age = (cap - pub).total_seconds() / 3600.0
            if age <= 0:
                continue
            title = split_title(d["title"])
            paras, avg_para, subhead = body_stats(d["content"], d["text_length"])
            lower = title.lower()
            d.update({
                "title": title,
                "title_len": len(title),
                "has_digit": any(ch.isdigit() for ch in title),
                "has_question": ("?" in title) or ("？" in title) or title.endswith(("吗", "呢")),
                "has_exclaim": ("!" in title) or ("！" in title),
                "has_colon": any(ch in title for ch in (":", "：", "|", "｜")),
                "has_quote": any(ch in title for ch in ("“", "「", "『")),
                "has_contrast": any(w.lower() in lower for w in CONTRAST_WORDS),
                "has_emotion": any(w in title for w in EMOTION_WORDS),
                "body_chars": int(d["text_length"] or 0) or len(re.sub(r"\s+", "", d["content"] or "")),
                "paragraphs": paras,
                "avg_para": avg_para,
                "has_subhead": subhead,
                "has_image": has_image(d["content"]),
                "age_hours": age,
                "age_bucket": min(12, int(math.log2(max(age, 1.0)))),
                "metric_source": d["data_source"],
                "hour": pub.hour,
                "weekday": pub.weekday(),
                "slot": ("早6-9" if 6 <= pub.hour < 9 else
                         "午11-14" if 11 <= pub.hour < 14 else
                         "晚17-22" if 17 <= pub.hour < 22 else "其他"),
                "source_type": source_type(d["source"]),
                "log_reads": math.log10(d["reads"]),
            })
            rows.append(d)
        con.close()
    return rows


def debias(rows, key):
    """在 (类目,龄段[,来源类型]) 内减去中位数,得到 age/category 去偏后的 log 读数。"""
    groups = {}
    for r in rows:
        g = tuple(r[k] for k in key)
        groups.setdefault(g, []).append(r["log_reads"])
    med = {g: median(v) for g, v in groups.items()}
    for r in rows:
        g = tuple(r[k] for k in key)
        r["adj_log"] = r["log_reads"] - med[g]


# ------------------------------------------------------------------ 分析
BINARY = [
    ("has_digit", "标题含数字"),
    ("has_question", "标题是疑问句/提问"),
    ("has_exclaim", "标题含感叹号"),
    ("has_colon", "标题含冒号/竖线"),
    ("has_quote", "标题含引号"),
    ("has_contrast", "标题含对比/转折词"),
    ("has_emotion", "标题含情绪/冲突词"),
    ("has_subhead", "正文含小标题加粗"),
    ("has_image", "正文含图片"),
]


def tier_flags(rows):
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)
    for cat, rs in by_cat.items():
        vals = sorted(r["reads"] for r in rs)
        q90 = quantile(vals, 0.9)
        q50 = quantile(vals, 0.5)
        for r in rs:
            r["is_top"] = r["reads"] >= q90
            r["is_low"] = r["reads"] < q50


def binary_report(rows):
    out = []
    for field, label in BINARY:
        a = [r["adj_log"] for r in rows if r[field]]
        b = [r["adj_log"] for r in rows if not r[field]]
        ci = welch_ci(a, b)
        top1 = sum(1 for r in rows if r[field] and r["is_top"])
        top0 = sum(1 for r in rows if not r[field] and r["is_top"])
        low1 = sum(1 for r in rows if r[field] and r["is_low"])
        low0 = sum(1 for r in rows if not r[field] and r["is_low"])
        # 分档占比用组内比例
        top_rate1 = top1 / len(a) if a else None
        top_rate0 = top0 / len(b) if b else None
        diff_top = newcombe_diff(top1, len(a), top0, len(b)) if a and b else None
        per_cat = []
        for cat in CATS:
            ca = [r["adj_log"] for r in rows if r["category"] == cat and r[field]]
            cb = [r["adj_log"] for r in rows if r["category"] == cat and not r[field]]
            if len(ca) >= 30 and len(cb) >= 30:
                c = welch_ci(ca, cb)
                per_cat.append({"category": cat, "n1": len(ca), "n0": len(cb), "diff": c["diff"]})
        signs = [1 if p["diff"] > 0 else -1 for p in per_cat]
        stable = (
            ci is not None and (ci["lo"] > 0 or ci["hi"] < 0)
            and len(per_cat) >= 3 and abs(sum(signs)) == len(signs)
        )
        out.append({
            "feature": field,
            "label": label,
            "n1": len(a),
            "n0": len(b),
            "insufficient": len(a) < 30 or len(b) < 30,
            "adj_log_diff": None if ci is None else round(ci["diff"], 4),
            "adj_log_diff_ci95": None if ci is None else [round(ci["lo"], 4), round(ci["hi"], 4)],
            "ratio_of_median_reads": None if ci is None else round(10 ** ci["diff"], 3),
            "median_reads_1": int(median([r["reads"] for r in rows if r[field]])) if a else None,
            "median_reads_0": int(median([r["reads"] for r in rows if not r[field]])) if b else None,
            "top_decile_rate_1": None if top_rate1 is None else round(top_rate1, 4),
            "top_decile_rate_0": None if top_rate0 is None else round(top_rate0, 4),
            "top_decile_diff": None if diff_top is None else round(diff_top["diff"], 4),
            "top_decile_diff_ci95": None if diff_top is None else [round(diff_top["lo"], 4), round(diff_top["hi"], 4)],
            "per_category_diff": per_cat,
            "per_category_sign_consistent": len(per_cat) >= 3 and abs(sum(signs)) == len(signs),
            "stable": stable,
        })
    return out


def continuous_report(rows):
    specs = [
        ("title_len", "标题字数"),
        ("body_chars", "正文字数"),
        ("paragraphs", "正文段数"),
        ("avg_para", "平均段长"),
        ("age_hours", "发布→采集间隔(小时)"),
    ]
    out = []
    for field, label in specs:
        vals = [float(r[field]) for r in rows]
        target = [r["adj_log"] if field != "age_hours" else r["log_reads"] for r in rows]
        sp = spearman(vals, target)
        out.append({"feature": field, "label": label, "spearman": sp,
                    "median": round(median(vals), 2),
                    "p10": round(quantile(vals, 0.1), 2), "p90": round(quantile(vals, 0.9), 2)})
    return out


def bucket_report(rows):
    """标题句式桶(question/plain/number)与阅读量的关系,同时看现有桶顺序是否对齐。"""
    out = {}
    for cat_filter in [None] + CATS:
        rs = [r for r in rows if cat_filter is None or r["category"] == cat_filter]
        if not rs:
            continue
        groups = {"question": [], "number": [], "plain": []}
        for r in rs:
            if r["has_question"]:
                groups["question"].append(r["reads"])
            elif r["has_digit"]:
                groups["number"].append(r["reads"])
            else:
                groups["plain"].append(r["reads"])
        out[cat_filter or "ALL"] = {
            k: {"n": len(v), "median_reads": int(median(v)) if v else None,
                "p75_reads": int(quantile(v, 0.75)) if v else None}
            for k, v in groups.items()
        }
    return out


def source_split(rows):
    table = {}
    for r in rows:
        key = (r["category"], r["source_type"])
        table.setdefault(key, []).append(r)

    def summarize(rs):
        return {
            "n": len(rs),
            "median_reads": int(median([r["reads"] for r in rs])),
            "median_body": int(median([r["body_chars"] for r in rs])),
            "has_image_rate": round(sum(1 for r in rs if r["has_image"]) / len(rs), 3),
            "has_digit_rate": round(sum(1 for r in rs if r["has_digit"]) / len(rs), 3),
        }

    return {
        "|".join(map(str, k)): summarize(v)
        for k, v in sorted(table.items(), key=lambda kv: -len(kv[1]))
    }


def boot_median_diff(a, b, reps=800, seed=42):
    """中位数差 a-b 的 bootstrap 95%CI(只用于描述分档差异,不用于生产)。"""
    import random
    if len(a) < 30 or len(b) < 30:
        return None
    rng = random.Random(seed)
    a = list(a)
    b = list(b)
    na, nb = min(len(a), 4000), min(len(b), 4000)
    diffs = []
    for _ in range(reps):
        sa = [a[rng.randrange(len(a))] for _ in range(na)]
        sb = [b[rng.randrange(len(b))] for _ in range(nb)]
        diffs.append(median(sa) - median(sb))
    diffs.sort()
    return [round(diffs[int(0.025 * reps)], 2), round(diffs[int(0.975 * reps)], 2)]


def tier_profile(rows):
    """爆款档(类目内 top10%)与低阅读档(类目内 <p50)的特征画像,带比例差 95%CI。"""
    top = [r for r in rows if r["is_top"]]
    low = [r for r in rows if r["is_low"]]

    def num(rs, field):
        vals = [float(r[field]) for r in rs]
        return {"n": len(vals), "median": round(median(vals), 2),
                "p25": round(quantile(vals, 0.25), 2), "p75": round(quantile(vals, 0.75), 2)}

    profile = {
        "top": {"n": len(top), **{f: num(top, f) for f in ("title_len", "body_chars", "paragraphs", "avg_para")}},
        "low": {"n": len(low), **{f: num(low, f) for f in ("title_len", "body_chars", "paragraphs", "avg_para")}},
    }
    cont = []
    for field in ("title_len", "body_chars", "paragraphs", "avg_para"):
        av = [float(r[field]) for r in top]
        bv = [float(r[field]) for r in low]
        cont.append({
            "feature": field,
            "median_top": round(median(av), 2) if av else None,
            "median_low": round(median(bv), 2) if bv else None,
            "median_diff": round(median(av) - median(bv), 2) if av and bv else None,
            "median_diff_boot_ci95": boot_median_diff(av, bv),
        })
    profile["continuous"] = cont
    rates = []
    for field, label in BINARY:
        k1 = sum(1 for r in top if r[field])
        k0 = sum(1 for r in low if r[field])
        d = newcombe_diff(k1, len(top), k0, len(low))
        rates.append({
            "feature": field, "label": label,
            "top_rate": round(k1 / len(top), 4), "low_rate": round(k0 / len(low), 4),
            "diff": None if d is None else round(d["diff"], 4),
            "diff_ci95": None if d is None else [round(d["lo"], 4), round(d["hi"], 4)],
            "insufficient": len(top) < 30 or len(low) < 30,
        })
    profile["rates"] = rates
    return profile


def joint_effects(rows):
    """把 9 个二元特征放进同一个线性概率模型(纯 Python 最小二乘),
    看"控制其他特征后"谁还站得住;只做解释,不用于生产评分。"""
    specs = [f for f, _ in BINARY if any(r[f] for r in rows)]
    X = [[1.0] + [1.0 if r[f] else 0.0 for f in specs] for r in rows]
    y = [r["adj_log"] for r in rows]
    k = len(X[0])
    # 正规方程
    xtx = [[sum(row[i] * row[j] for row in X) for j in range(k)] for i in range(k)]
    xty = [sum(row[i] * yy for row, yy in zip(X, y)) for i in range(k)]
    # 高斯消元
    for i in range(k):
        piv = max(range(i, k), key=lambda r_: abs(xtx[r_][i]))
        xtx[i], xtx[piv] = xtx[piv], xtx[i]
        xty[i], xty[piv] = xty[piv], xty[i]
        if abs(xtx[i][i]) < 1e-9:
            continue
        for r_ in range(k):
            if r_ == i:
                continue
            f = xtx[r_][i] / xtx[i][i]
            if f:
                for c_ in range(i, k):
                    xtx[r_][c_] -= f * xtx[i][c_]
                xty[r_] -= f * xty[i]
    beta = [xty[i] / xtx[i][i] if abs(xtx[i][i]) > 1e-9 else 0.0 for i in range(k)]
    return {"intercept": round(beta[0], 4),
            "coef_adj_log": {f: round(b, 4) for f, b in zip(specs, beta[1:])},
            "note": "线性概率模型,仅描述控制其余特征后的平均效应;不是因果,也不用于生产评分"}


def slot_report(rows):
    out = []
    for slot in ("早6-9", "午11-14", "晚17-22", "其他"):
        a = [r["adj_log"] for r in rows if r["slot"] == slot]
        b = [r["adj_log"] for r in rows if r["slot"] != slot]
        if not a or not b:
            continue
        ci = welch_ci(a, b)
        per_cat = []
        for cat in CATS:
            ca = [r["adj_log"] for r in rows if r["category"] == cat and r["slot"] == slot]
            cb = [r["adj_log"] for r in rows if r["category"] == cat and r["slot"] != slot]
            if len(ca) >= 30 and len(cb) >= 30:
                per_cat.append(welch_ci(ca, cb)["diff"])
        out.append({
            "slot": slot, "n": len(a),
            "adj_log_diff_vs_rest": round(ci["diff"], 4),
            "ci95": [round(ci["lo"], 4), round(ci["hi"], 4)],
            "ratio": round(10 ** ci["diff"], 3),
            "per_category_diff": [round(v, 4) for v in per_cat],
        })
    return out


def label_distribution():
    """现有 viral_labels 的取值分布与它对阅读量的信息量(只读)。"""
    out = {}
    for cat in CATS:
        con = sqlite3.connect(ROOT / "01_categories" / cat / "articles.db")
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT v.label, v.confidence_tier, v.score, m.reads FROM viral_labels v "
            "LEFT JOIN article_metrics m ON m.article_id=v.article_id"
        )]
        con.close()
        labels = {}
        for r in rows:
            labels[r["label"]] = labels.get(r["label"], 0) + 1
        scores = [r["score"] for r in rows if r["score"] is not None]
        modal = max(labels.values()) / len(rows) if rows and labels else None
        out[cat] = {
            "labeled": len(rows),
            "distinct_scores": len({round(s, 9) for s in scores}),
            "modal_label_share": None if modal is None else round(modal, 4),
            "label_counts": labels,
        }
    return out


def main():
    rows = load_rows()
    debias(rows, ("category", "age_bucket"))
    debias_source = [dict(r) for r in rows]
    for r in debias_source:
        r["_k"] = (r["category"], r["age_bucket"], r["source_type"])
    tmp = {}
    for r in debias_source:
        tmp.setdefault(r["_k"], []).append(r["log_reads"])
    med = {k: median(v) for k, v in tmp.items()}
    for r in debias_source:
        r["adj_log_source"] = r["log_reads"] - med[r["_k"]]
    tier_flags(rows)

    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)
    overview = {
        cat: {
            "n": len(rs),
            "median_reads": int(median([r["reads"] for r in rs])),
            "p90_reads": int(quantile([r["reads"] for r in rs], 0.9)),
            "median_age_hours": round(median([r["age_hours"] for r in rs]), 1),
            "p10_age_hours": round(quantile([r["age_hours"] for r in rs], 0.1), 1),
            "p90_age_hours": round(quantile([r["age_hours"] for r in rs], 0.9), 1),
        }
        for cat, rs in by_cat.items()
    }

    binary = binary_report(rows)

    # 同来源稳健性:在 (类目,龄段,来源) 内去偏后再算一次方向
    src_rows = debias_source
    for item in binary:
        field = item["feature"]
        a = [r["adj_log_source"] for r in src_rows if r[field]]
        b = [r["adj_log_source"] for r in src_rows if not r[field]]
        ci = welch_ci(a, b)
        item["same_source_adj_diff"] = None if ci is None else round(ci["diff"], 4)
        item["same_source_adj_diff_ci95"] = None if ci is None else [round(ci["lo"], 4), round(ci["hi"], 4)]
        item["same_source_sign"] = None if ci is None else ("+" if ci["diff"] > 0 else "-")

    # 只 retained legacy 导入群体(占 98.3%)再算一遍:排除少数公开页高阅读样本对结论的拉动
    imp = [r for r in rows if r["metric_source"] != "toutiao_public_article"]
    for item in binary:
        field = item["feature"]
        a = [r["adj_log"] for r in imp if r[field]]
        b = [r["adj_log"] for r in imp if not r[field]]
        ci = welch_ci(a, b)
        item["import_only_n1"] = len(a)
        item["import_only_n0"] = len(b)
        item["import_only_adj_diff"] = None if ci is None else round(ci["diff"], 4)
        item["import_only_ci95"] = None if ci is None else [round(ci["lo"], 4), round(ci["hi"], 4)]
        item["import_only_sign"] = None if ci is None else ("+" if ci["diff"] > 0 else "-")

    import_continuous = {}
    for field in ("title_len", "body_chars", "paragraphs", "avg_para"):
        import_continuous[field] = spearman([float(r[field]) for r in imp], [r["adj_log"] for r in imp])
    payload_import = {"n": len(imp), "continuous_spearman": import_continuous}

    age_sp = spearman([r["age_hours"] for r in rows], [r["log_reads"] for r in rows])
    title_len_by_band = {}
    for lo, hi in [(0, 10), (10, 16), (16, 22), (22, 30), (30, 100)]:
        rs = [r for r in rows if lo <= r["title_len"] < hi]
        if rs:
            title_len_by_band[f"{lo}-{hi}"] = {
                "n": len(rs),
                "median_reads": int(median([r["reads"] for r in rs])),
                "median_adj": round(median([r["adj_log"] for r in rs]), 3),
            }

    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "target": "article_metrics.reads(采集时点截面),log10;按(类目,log2 龄段)中位数去偏",
        "overview": overview,
        "metric_source_split": {
            src: sum(1 for r in rows if r["metric_source"] == src)
            for src in sorted({r["metric_source"] for r in rows})
        },
        "age_effect_spearman": age_sp,
        "title_length_bands": title_len_by_band,
        "binary_features": binary,
        "continuous_features": continuous_report(rows),
        "title_bucket_vs_reads": bucket_report(rows),
        "source_split": source_split(rows),
        "tier_profile": tier_profile(rows),
        "joint_effects": joint_effects(rows),
        "slot_effects": slot_report(rows),
        "existing_labels": label_distribution(),
        "import_only": payload_import,
        "caveats": [
            "读数是采集时点快照(发布后数小时到数年),不是统一 T+N;已按类目×log2 龄段去偏,但仍不能完全消除时间累积与平台推荐差异。",
            "样本里 98.3% 的读数来自 legacy_factory_search_ssr 批量导入,只有约 1.7% 来自公开页当面计数;两者口径不同,合并统计只作相关性参考。",
            "impressions 只有 finance 85 / social 134 等共 196 条,绝大多数样本只有 reads,不能算曝光率。",
            "favorites 全库 0 条非空,任何含收藏的指标都不可复核。",
            "has_image 与来源强共线,且当前读数样本里只有 6 篇正文含图,图片效应无法估计(样本不足)。",
            "35k 样本来自若干固定站点/账号,不能外推到整个平台;所有效应是相关性,不是因果。",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({k: payload[k] for k in ("overview", "age_effect_spearman", "title_length_bands")},
                     ensure_ascii=False, indent=1))
    for item in binary:
        print(f"{item['label']:<14} n1={item['n1']:<6} n0={item['n0']:<6} "
              f"diff={item['adj_log_diff']} CI={item['adj_log_diff_ci95']} "
              f"ratio={item['ratio_of_median_reads']} stable={item['stable']} "
              f"same_src={item['same_source_adj_diff']}")
    print("bucket:", json.dumps(payload["title_bucket_vs_reads"], ensure_ascii=False))
    print("continuous:")
    for item in payload["continuous_features"]:
        print(" ", item["label"], item["spearman"])
    print("tier:", json.dumps(payload["tier_profile"], ensure_ascii=False, indent=1))
    print("joint:", json.dumps(payload["joint_effects"], ensure_ascii=False))
    print("slot:", json.dumps(payload["slot_effects"], ensure_ascii=False))
    print("labels:", json.dumps(payload["existing_labels"], ensure_ascii=False))
    print("per-cat binary diffs:")
    for item in binary:
        print(" ", item["feature"], [(p["category"], round(p["diff"], 3)) for p in item["per_category_diff"]])
    print("saved", OUT)


if __name__ == "__main__":
    main()
