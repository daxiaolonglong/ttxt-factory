# -*- coding: utf-8 -*-
"""P31 阶段二:关键词覆盖的匹配口径钉死(先红后绿)。

实测取证(2026-11-16,`07_tmp/p31_phase2/`,汇总在 Wiki 报告里):
- 现库 532 条真实文章里,标题**全部**原样出现在清洗正文里 ⇒ 当下"漏计标题"这一条为 0;
- 清洗吃掉命中、大小写差异这两类漏计也都是 0;
- technology 的 seed 词「效率工具」在 231 条文章里正文出现 0 次、标题出现 0 次
  ⇒ 覆盖 0 是事实,不是匹配代码的 bug(该词是死词,换词要有语料证据,不许靠放宽匹配凑数)。

所以本轮改的是**语义加固 + 口径钉死**,不是把 0 改成非 0:
- 匹配必须同时看标题与正文(防未来清洗器把标题从正文里剥掉;当下实测 0 条受影响);
- 一篇文章只能算一次(标题在正文内时不得重复计数);
- 严禁模糊/分词凑数:「效率工具」不得因为正文里有「工具」「效率」就算命中;
- 大小写敏感、子串匹配是当下语义,改动必须同时改测试并给实测依据;
- 评分按类目库隔离,空关键词不得匹配全库。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ttxt.analysis import keywords
from ttxt.crawler import store

NOW = "2026-11-16T10:00:00+08:00"
LONG_BODY = "内容" * 70  # 140 字符,保证 quality 判定(≥120)不误伤


def _db(tmp_path: Path, category: str, rows: list[dict], keyword_list: list[str]) -> Path:
    db_path = tmp_path / f"{category}.db"
    store.migrate_articles_db(category, db_path=db_path)
    con = sqlite3.connect(db_path)
    try:
        for index, row in enumerate(rows):
            url = f"https://x.test/{category}/{index}"
            body = row.get("cleaned", "")
            raw = row.get("content", body)
            con.execute(
                "INSERT INTO articles(category, source, title, content, cleaned_content, text_length,"
                " url, url_normalized, content_hash, fetched_at) VALUES(?, 'src', ?, ?, ?, ?, ?, ?, ?, ?)",
                (category, row.get("title", ""), raw, body, len(body), url, url, f"h{category}{index}", NOW),
            )
        for keyword in keyword_list:
            con.execute(
                "INSERT INTO keywords(keyword, category, origin, status, created_at)"
                " VALUES(?, ?, 'seed', 'active', ?)",
                (keyword, category, NOW),
            )
        con.commit()
    finally:
        con.close()
    return db_path


def _performance(db_path: Path) -> dict[str, dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return {
            row["keyword"]: dict(row)
            for row in con.execute(
                "SELECT k.keyword, p.collected, p.quality_articles, p.avg_text_length FROM keywords k"
                " JOIN keyword_performance p ON p.keyword_id = k.id"
            ).fetchall()
        }
    finally:
        con.close()


# ------------------------------------------------------------------ 红:标题不参与匹配

def test_keyword_in_title_only_still_counts_as_coverage(tmp_path):
    """清洗器把标题从正文里剥掉时(未来某个来源的真实形态),标题命中必须算覆盖。

    实测当下不存在这种形态(532/532 标题都在正文内),所以这条测试对现网是 0 影响,
    但它是防"清洗器一改、覆盖悄悄掉一半"的护栏。
    """
    db_path = _db(
        tmp_path,
        "technology",
        [{"title": "五款效率工具横评", "cleaned": LONG_BODY, "content": LONG_BODY}],
        ["效率工具"],
    )
    stats = keywords.score_keywords("technology", db_path=db_path)
    assert stats["zero_coverage"] == 0, "标题命中被漏计 ⇒ 覆盖统计只看正文"
    assert _performance(db_path)["效率工具"]["collected"] == 1


# ------------------------------------------------------------------ 绿也要钉死的行为

def test_title_inside_body_is_counted_exactly_once(tmp_path):
    """标题原样出现在正文里(现网真实形态)⇒ 一篇文章只能算一次,不得重复计数。"""
    title = "效率工具横评"
    db_path = _db(
        tmp_path,
        "technology",
        [{"title": title, "cleaned": f"{title} {LONG_BODY}", "content": f"{title} {LONG_BODY}"}],
        ["效率工具"],
    )
    keywords.score_keywords("technology", db_path=db_path)
    assert _performance(db_path)["效率工具"]["collected"] == 1


def test_body_hit_is_counted(tmp_path):
    """R57 口径:仅正文命中仍进覆盖(不静默消失)、单列 weak,但**不计贡献**(collected 只数标题命中)。"""
    rows = [{"title": "无关标题", "cleaned": f"{LONG_BODY} 效率工具 {LONG_BODY}"} for _ in range(3)]
    db_path = _db(tmp_path, "technology", rows, ["效率工具"])
    stats = keywords.score_keywords("technology", db_path=db_path)
    detail = stats["viral"]["details"]["效率工具"]
    assert detail["coverage_articles"] == 3, "正文命中必须进覆盖统计(判定闸/准入判定用这个口径),不得静默消失"
    assert detail["weak_content_hits"] == 3, "仅正文命中必须单列 weak,可审计"
    assert _performance(db_path)["效率工具"]["collected"] == 0, "仅正文命中不计贡献(R57:准入看区分度)"


def test_no_fuzzy_matching_to_inflate_coverage(tmp_path):
    """「效率工具」不得因为正文里分别有「效率」「工具」就算命中 —— 放宽匹配 = 伪造证据。"""
    rows = [
        {"title": "效率很重要", "cleaned": f"效率 {LONG_BODY}"},
        {"title": "工具合集", "cleaned": f"工具 {LONG_BODY}"},
        {"title": "效率与工具", "cleaned": f"效率与工具 {LONG_BODY}"},
    ]
    db_path = _db(tmp_path, "technology", rows, ["效率工具"])
    stats = keywords.score_keywords("technology", db_path=db_path)
    assert stats["zero_coverage"] == 1
    assert _performance(db_path)["效率工具"]["collected"] == 0
    assert "效率工具" in stats["feedback_candidates"]


def test_substring_semantics_is_pinned(tmp_path):
    """子串匹配是当下语义:「数据」命中「数据库」;要改成语义边界匹配必须同时改这条测试。"""
    db_path = _db(tmp_path, "technology", [{"title": "数据库", "cleaned": f"数据库 {LONG_BODY}"}], ["数据"])
    keywords.score_keywords("technology", db_path=db_path)
    assert _performance(db_path)["数据"]["collected"] == 1


def test_case_sensitivity_is_pinned(tmp_path):
    """大小写敏感是当下语义(实测现网 0 条因此漏计);改成不敏感必须带实测依据并同步改测试。"""
    db_path = _db(tmp_path, "technology", [{"title": "ai 应用", "cleaned": f"ai 应用 {LONG_BODY}"}], ["AI"])
    keywords.score_keywords("technology", db_path=db_path)
    assert _performance(db_path)["AI"]["collected"] == 0


def test_empty_body_title_hit_has_zero_quality(tmp_path):
    """只有标题命中、正文为空:覆盖算 1,但正文质量算 0、平均长度为 0 —— 不许把它当好文章。"""
    db_path = _db(tmp_path, "health", [{"title": "睡眠与运动", "cleaned": "", "content": ""}], ["睡眠"])
    keywords.score_keywords("health", db_path=db_path)
    row = _performance(db_path)["睡眠"]
    assert row["collected"] == 1
    assert row["quality_articles"] == 0
    assert row["avg_text_length"] == 0.0


def test_empty_keyword_matches_nothing(tmp_path):
    """空关键词不得匹配全库(否则覆盖数变成文章总数,评分被污染)。"""
    db_path = _db(tmp_path, "social", [{"title": "城市观察", "cleaned": f"城市 {LONG_BODY}"}], [""])
    keywords.score_keywords("social", db_path=db_path)
    assert _performance(db_path)[""]["collected"] == 0


def test_scoring_is_isolated_per_category_db(tmp_path):
    """类目隔离:technology 的评分不得看见 social 库里的文章。"""
    tech = _db(tmp_path, "technology", [{"title": "无关", "cleaned": LONG_BODY}], ["效率工具"])
    social = _db(
        tmp_path,
        "social",
        [{"title": "效率工具在城市里的使用", "cleaned": f"效率工具 {LONG_BODY}"}],
        ["效率工具"],
    )
    keywords.score_keywords("technology", db_path=tech)
    keywords.score_keywords("social", db_path=social)
    assert _performance(tech)["效率工具"]["collected"] == 0
    assert _performance(social)["效率工具"]["collected"] == 1
