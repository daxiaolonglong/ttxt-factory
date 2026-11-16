# -*- coding: utf-8 -*-
"""P28:真实爆款表现要喂回关键词评分(先红后绿)。

用户目标第 2 条:模型必须知道什么是爆款、什么有潜力、什么是垃圾,并把结论反馈到选词。
实测缺口(2026-11-16 复核):``score_keywords()`` 只用 覆盖数 + 有效正文率,
**完全不读 ``viral_labels``** ⇒ 关键词采到再多真爆款也不加分,采到一堆垃圾也不减分。

护栏口径(本轮定死):
- 只用 ``confidence_tier='measured'`` 且 ``score`` 非空 的真实测得样本;
- ``unscored``/``missing_data``/``incomplete_data``/``no_impressions`` **不进分母**,更不当 0 分;
- 类目内真实测得样本 < ``viral.MIN_CALIBRATION_SAMPLE``(30)⇒ **不宣称已标定,不参与评分**;
- 关键词自身命中的真实测得文章 < 3 篇 ⇒ 该词不加爆款项;
- 评分仍按类目隔离(各库各算),gap 判定、恢复、24 小时冷却一律不变。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ttxt.analysis import keywords, viral
from ttxt.crawler import store

# 默认阈值(未校准)下能落进各标签的分数区间:verified ≥0.15 / potential ≥0.08 / normal ≥0.02
GOOD_SCORES = {"verified_viral": 0.42, "potential": 0.12}


def _make_db(tmp_path: Path, category: str, texts: list[str], keywords_list: list[str]) -> Path:
    db_path = tmp_path / f"{category}.db"
    store.migrate_articles_db(category, db_path=db_path)
    now = "2026-11-16T10:00:00+08:00"
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        for index, text in enumerate(texts):
            con.execute(
                "INSERT INTO articles(category, source, title, content, cleaned_content, text_length,"
                " url, url_normalized, content_hash, fetched_at) VALUES(?, 'src', ?, ?, ?, ?, ?, ?, ?, ?)",
                (category, f"标题{index}", text, text, len(text), f"https://x.test/{category}/{index}",
                 f"https://x.test/{category}/{index}", f"h{category}{index}", now),
            )
        for keyword in keywords_list:
            con.execute(
                "INSERT INTO keywords(keyword, category, origin, status, created_at) VALUES(?, ?, 'seed', 'active', ?)",
                (keyword, category, now),
            )
        con.commit()
    finally:
        con.close()
    return db_path


def _labels(db_path: Path, per_keyword: dict[str, list[str]], filler_measured: int = 0) -> None:
    """给命中某关键词的文章打标签;filler_measured = 另外造多少条真实测得样本(凑类目门槛)。

    只写 measured + score 非空;不打标签的文章 = 生产里的 unscored/missing_data(压根没有指标)。
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        now = "2026-11-16T10:05:00+08:00"
        used: set[int] = set()
        for keyword, labels in per_keyword.items():
            rows = con.execute(
                "SELECT id FROM articles WHERE cleaned_content LIKE ? ORDER BY id", (f"%{keyword}%",)
            ).fetchall()
            for row, label in zip(rows, labels):
                score = GOOD_SCORES.get(label, 0.005 if label == "low_quality" else 0.04)
                con.execute(
                    "INSERT OR REPLACE INTO viral_labels(article_id, label, confidence_tier, score, reason,"
                    " features_json, labeled_at) VALUES(?, ?, 'measured', ?, '测试构造的真实测得样本', '{}', ?)",
                    (row["id"], label, score, now),
                )
                used.add(row["id"])
        if filler_measured:
            # 只拿真正的凑数文章凑类目样本,不许顺手把同一批命中文章也算进去
            spare_ids_sql = ",".join(str(i) for i in used) or "-1"
            spare = [row["id"] for row in con.execute(
                "SELECT id FROM articles WHERE cleaned_content LIKE ?"
                f" AND id NOT IN ({spare_ids_sql}) ORDER BY id", ("%无关文章%",)
            ).fetchall()]
            for row_id in spare[:filler_measured]:
                con.execute(
                    "INSERT OR REPLACE INTO viral_labels(article_id, label, confidence_tier, score, reason,"
                    " features_json, labeled_at) VALUES(?, 'normal', 'measured', 0.04, '凑类目样本门槛', '{}', ?)",
                    (row_id, now),
                )
        con.commit()
    finally:
        con.close()


def _rows(db_path: Path) -> dict[str, dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return {
            row["keyword"]: dict(row)
            for row in con.execute(
                "SELECT k.keyword, p.collected, p.quality_articles, p.score FROM keywords k"
                " JOIN keyword_performance p ON p.keyword_id = k.id"
            ).fetchall()
        }
    finally:
        con.close()


def _articles(n_per_kw: int, keyword: str, filler_tag: str = "凑数") -> list[str]:
    """每个关键词 n 篇命中文章,正文够长(≥120 字符)保证 quality=collected;不同关键词文章不重叠。"""
    body = "内容" * 70
    return [f"{keyword} 第{i}篇 {body} {filler_tag}{i}" for i in range(n_per_kw)]


def _fillers(n: int) -> list[str]:
    """不含任何关键词的文章:用来给类目凑真实测得样本门槛。"""
    return [f"无关文章{i} " + "内容" * 70 for i in range(n)]


# ---------------------------------------------------------------- 红:现状不读爆款标签

def test_measured_viral_performance_changes_the_score(tmp_path):
    """同样覆盖数、同样正文质量的两个关键词:采到真爆款的词必须高于采到一堆垃圾的词。

    老公式下这两个词**同分**(覆盖 4 篇、正文都合格),这正是红证据要证的缺口。
    """
    texts = _articles(4, "开源") + _articles(4, "八卦") + _fillers(40)
    db_path = _make_db(tmp_path, "demo", texts, ["开源", "八卦"])
    # 8 篇命中文章打上真实测得标签,再凑 22 条 ⇒ 类目样本 30,刚好过标定门槛
    _labels(db_path, {"开源": ["verified_viral", "verified_viral", "potential", "potential"],
                      "八卦": ["low_quality", "low_quality", "low_quality", "low_quality"]},
            filler_measured=22)
    stats = keywords.score_keywords("demo", db_path=db_path)
    rows = _rows(db_path)
    assert stats["viral"]["status"] == "applied"
    assert stats["viral"]["details"]["开源"]["measured_hits"] == 4
    assert stats["viral"]["details"]["开源"]["signal"] == pytest.approx(
        (1.0 + 1.0 + 0.6 + 0.6) / 4, abs=1e-6)
    assert stats["viral"]["details"]["八卦"]["signal"] == 0.0
    # 两个词覆盖与正文质量完全相同 ⇒ 分数差只能来自真实爆款表现
    assert rows["开源"]["collected"] == rows["八卦"]["collected"]
    assert rows["开源"]["quality_articles"] == rows["八卦"]["quality_articles"]
    assert rows["开源"]["score"] > rows["八卦"]["score"]
    # 且差值就是加权公式:base=1.0 时 0.7*1+0.3*signal
    assert rows["开源"]["score"] == pytest.approx(0.7 + 0.3 * 0.8, abs=1e-4)
    assert rows["八卦"]["score"] == pytest.approx(0.7, abs=1e-4)


def test_real_low_quality_articles_drag_the_score_down(tmp_path):
    """真实测得的低质文章不能被当成爆款:同样的覆盖与正文质量,低质词评分要低于无数据词。"""
    texts = _articles(4, "开源") + _articles(4, "八卦") + _fillers(40)
    db_path = _make_db(tmp_path, "demo", texts, ["开源", "八卦"])
    _labels(db_path, {"八卦": ["low_quality", "low_quality", "low_quality", "low_quality"]},
            filler_measured=26)
    stats = keywords.score_keywords("demo", db_path=db_path)
    rows = _rows(db_path)
    base_no_data = rows["开源"]["score"]          # 无真实测得命中 ⇒ 原公式分
    assert stats["viral"]["details"]["八卦"]["signal"] == 0.0
    assert rows["八卦"]["score"] < base_no_data
    assert stats["viral"]["details"]["开源"]["signal"] is None


def test_unscored_labels_are_not_counted_as_zero(tmp_path):
    """没有真实指标的文章(unscored/missing_data/score=NULL)不进分母,更不当 0 分(规则 6)。"""
    texts = _articles(4, "开源") + [f"凑数文章{i} " + "内容" * 70 for i in range(32)]
    db_path = _make_db(tmp_path, "demo", texts, ["开源"])
    con = sqlite3.connect(db_path)
    try:
        # 命中"开源"的 4 篇:有标签行,但没有真实数据 ⇒ 只能算"没测得",不能算 0 分
        hot_ids = [r[0] for r in con.execute(
            "SELECT id FROM articles WHERE cleaned_content LIKE '%开源%' ORDER BY id").fetchall()]
        for article_id in hot_ids:
            con.execute(
                "INSERT INTO viral_labels(article_id, label, confidence_tier, score, reason, features_json,"
                " labeled_at) VALUES(?, 'unscored', 'missing_data', NULL, '缺少真实表现指标', '{}',"
                " '2026-11-16T10:05:00+08:00')", (article_id,),
            )
        # 类目里另外 30 篇有真实测得样本 ⇒ 门槛已过,只看这个词自己有没有真实命中
        spare_ids = [r[0] for r in con.execute(
            "SELECT id FROM articles WHERE cleaned_content LIKE '%凑数文章%' ORDER BY id").fetchall()]
        for article_id in spare_ids[:30]:
            con.execute(
                "INSERT INTO viral_labels(article_id, label, confidence_tier, score, reason, features_json,"
                " labeled_at) VALUES(?, 'normal', 'measured', 0.04, '测试构造的真实测得样本', '{}',"
                " '2026-11-16T10:05:00+08:00')", (article_id,),
            )
        con.commit()
    finally:
        con.close()
    stats = keywords.score_keywords("demo", db_path=db_path)
    row = _rows(db_path)["开源"]
    detail = stats["viral"]["details"]["开源"]
    assert stats["viral"]["measured_samples"] == 30   # unscored 的 4 条不进真实测得样本数
    assert detail["matched_articles"] == 4            # 覆盖统计照旧(4 篇命中)
    assert detail["measured_hits"] == 0               # 但真实测得命中为 0
    assert detail["signal"] is None
    assert stats["viral"]["status"] == "no_measured_hits"
    assert row["score"] == pytest.approx(1.0, abs=1e-6)  # 原公式:0.6*1 + 0.4*1,不因缺数据被拉低


def test_category_below_calibration_sample_must_not_claim_calibration(tmp_path):
    """真实测得样本 < 30 的类目:不许把爆款项当成已标定信号用,评分保持原公式并如实标注。"""
    texts = _articles(4, "开源") + _fillers(40)
    db_path = _make_db(tmp_path, "demo", texts, ["开源"])
    # 4 篇真爆款 + 25 条凑样 = 29 条真实测得 ⇒ 差 1 条就不许用爆款项
    _labels(db_path, {"开源": ["verified_viral", "verified_viral", "verified_viral", "verified_viral"]},
            filler_measured=viral.MIN_CALIBRATION_SAMPLE - 5)
    stats = keywords.score_keywords("demo", db_path=db_path)
    assert stats["viral"]["status"] == "insufficient_sample"
    assert stats["viral"]["measured_samples"] == viral.MIN_CALIBRATION_SAMPLE - 1
    assert stats["viral"]["required"] == viral.MIN_CALIBRATION_SAMPLE
    assert stats["viral"]["details"]["开源"]["signal"] is None
    assert "不宣称已标定" in stats["viral"]["details"]["开源"]["reason"]
    assert _rows(db_path)["开源"]["score"] == pytest.approx(1.0, abs=1e-6)


def test_keyword_with_too_few_measured_hits_gets_no_signal(tmp_path):
    """只有 1 篇真实测得命中(其余没指标)⇒ 词级样本不足,不加权,并记录原因。"""
    texts = _articles(4, "开源") + _fillers(30)
    db_path = _make_db(tmp_path, "demo", texts, ["开源"])
    _labels(db_path, {"开源": ["verified_viral"]}, filler_measured=viral.MIN_CALIBRATION_SAMPLE - 1)
    stats = keywords.score_keywords("demo", db_path=db_path)
    detail = stats["viral"]["details"]["开源"]
    assert stats["viral"]["measured_samples"] >= viral.MIN_CALIBRATION_SAMPLE
    assert keywords.MIN_KEYWORD_MEASURED_HITS > 1
    assert detail["matched_articles"] == 4
    assert detail["measured_hits"] == 1
    assert detail["signal"] is None
    assert "不足" in detail["reason"]
    assert _rows(db_path)["开源"]["score"] == pytest.approx(1.0, abs=1e-6)


def test_categories_do_not_pollute_each_other(tmp_path):
    """类目隔离:同样的文章摆进两个库,只有打了真实爆款标签的那个库评分变化。"""
    texts = _articles(4, "开源") + _fillers(30)
    hot = _make_db(tmp_path, "technology", texts, ["开源"])
    cold = _make_db(tmp_path, "demo", texts, ["开源"])
    # 2 篇验证爆款 + 2 篇真低质 ⇒ signal 0.5,评分必然离开原公式的 1.0
    _labels(hot, {"开源": ["verified_viral", "verified_viral", "low_quality", "low_quality"]},
            filler_measured=26)
    stats_hot = keywords.score_keywords("technology", db_path=hot)
    stats_cold = keywords.score_keywords("demo", db_path=cold)
    assert stats_hot["viral"]["status"] == "applied"
    assert stats_hot["viral"]["details"]["开源"]["signal"] == pytest.approx(0.5, abs=1e-6)
    assert _rows(hot)["开源"]["score"] == pytest.approx(0.7 + 0.3 * 0.5, abs=1e-4)
    # 另一个库:没有一行真实测得样本 ⇒ 评分保持原公式,一点没被隔壁类目带跑
    assert stats_cold["viral"]["measured_samples"] == 0
    assert stats_cold["viral"]["status"] == "no_measured_hits"
    assert stats_cold["viral"]["details"]["开源"]["signal"] is None
    assert _rows(cold)["开源"]["score"] == pytest.approx(1.0, abs=1e-6)


def test_gap_and_feedback_candidates_unchanged_by_new_score(tmp_path):
    """新评分不得改动 gap 判定/反哺候选(那只看 collected,与分数无关)。"""
    texts = _articles(4, "开源") + _fillers(30)
    db_path = _make_db(tmp_path, "demo", texts, ["开源", "冷门词"])
    _labels(db_path, {"开源": ["verified_viral", "verified_viral", "verified_viral", "verified_viral"]},
            filler_measured=26)
    stats = keywords.score_keywords("demo", db_path=db_path)
    assert stats["feedback_candidates"] == ["冷门词"]
    assert stats["zero_coverage"] == 1
