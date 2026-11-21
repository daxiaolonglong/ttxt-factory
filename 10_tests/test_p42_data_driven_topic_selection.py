# -*- coding: utf-8 -*-
"""P42(阶段2 R64):选题由库里数据提出,并且先自评语料支持度再烧模型。

实测缺口(2026-11-21):R63 的 10 主题烟测 **0/10 写成**;R50 归因里 59.3% 的 critical
锚点在绑定来源全文里根本找不到 —— 根因是**语料缺口 + 人工出题**,不是模型能力。
R64-1 已把离题素材挡在素材池外,但「出什么题」仍是人拍脑袋,闭环没接上。

钉死的口径(先声明后取数):
- 候选主题只来自库里真实数据:``keyword_performance`` 战绩榜(score 降序、带 ``origin`` 溯源,
  缺战绩如实报空)+ ``--topic`` 人工补题;
- 爆款标题**不进候选池**:``viral_labels`` 里 ``measured``/``measured_reads`` 档且标签为
  ``verified_viral``/``potential`` 的**全文**文章标题只作参考线索(``used_for`` 写明),
  垃圾 ``low_quality`` 与中性 ``normal`` 逐个数并排除 —— 2026-11-21 social 实测:28 个候选里
  被拦 11 个,其中 10 个是整条标题(FTS 只召回它自己),另 2 条整标题靠常见二字组蒙过门禁;
  「爆款 → 词」的既有通道是 ``analyze → keywords.mine_keywords``(origin='mined'),不重造;
- 支持度判定必须复用写作同一条链与同一个门禁实现:
  ``rag.search(MATERIAL_POOL_LIMIT) → workflow._rank_materials(topic=…) → workflow._material_gate``,
  阈值只从 ``config.writing`` 读(6 全文 / 3 独立来源),本模块不新增也不放宽判定;
- 排序规则先声明:放行优先 ⇒ kept_full ⇒ distinct_sources ⇒ kept ⇒ 来源战绩分;
- 被拦候选只输出采集建议命令(crawl-plan 干跑),绝不自动入队、绝不触发调度器;
- 全程只读类目库:不调模型、不写业务表、不产草稿。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import pytest

from ttxt import config as cfg
from ttxt.__main__ import cmd_topics
from ttxt.analysis import rag, topics, viral
from ttxt.crawler import cleaner, store
from ttxt.writing import workflow

CATEGORY = "demo"
NOW = "2026-11-21T09:00:00+08:00"

HOT_TOPIC = "开源模型 训练 成本"
HOT_SENTENCE = "开源模型训练成本下降,行业观察。"  # 每句 4 个非重叠命中(开源/模型/训练/成本)
COLD_TOPIC = "邻里纠纷 索赔"  # 库里一个字都没有
# FTS 能召回(4 个 token ⇒ AND 扑空后走 OR 兜底)、内容却撑不起引句(生产 14:36 的形状)
DRIFT_TOPIC = "装修 噪音 投诉 赔偿"
DRIFT_BODY = "副业收益怎么算,这笔账要说清楚。" * 30 + "顺带提一句装修。"


def _insert(con, category: str, marker: str, host: str, title: str, text: str) -> int:
    url = f"https://{host}/a-{marker}"
    cur = con.execute(
        "INSERT INTO articles(category, source, title, content, cleaned_content, text_length,"
        " completeness, author, url, url_normalized, content_hash, fetched_at)"
        " VALUES(?, 'src', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            category, title, text, text, cleaner.usable_length(text),
            cleaner.completeness_of(text), None, url, url, f"hash-{category}-{marker}", NOW,
        ),
    )
    return int(cur.lastrowid)


def _corpus(tmp_path: Path, *, hot: int = 6, hot_hosts: tuple[str, ...] = ("h0.test", "h1.test", "h2.test"),
            drift: int = 0, cold_fillers: int = 2) -> tuple[Path, dict[str, int]]:
    """类目库:hot 篇真素材(跨 3 域名,刚好过 6/3 门槛)+ drift 篇离题稿 + 无关凑数稿。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / f"{CATEGORY}.db"
    store.migrate_articles_db(CATEGORY, db_path=db_path)
    ids: dict[str, int] = {}
    con = sqlite3.connect(db_path)
    try:
        for index in range(hot):
            ids[f"hot{index}"] = _insert(
                con, CATEGORY, f"hot{index}", hot_hosts[index % len(hot_hosts)],
                f"开源模型训练成本怎么降 观察{index}", HOT_SENTENCE * 30 + f"补充:hot{index}。",
            )
        for index in range(drift):
            ids[f"drift{index}"] = _insert(
                con, CATEGORY, f"drift{index}", f"d{index}.test", f"副业收益怎么算 {index}",
                DRIFT_BODY + f"标记 drift{index}。",
            )
        for index in range(cold_fillers):
            ids[f"filler{index}"] = _insert(
                con, CATEGORY, f"filler{index}", f"f{index}.test", f"行业观察随笔 {index}",
                "行业观察随笔记录一条。" * 80 + f"标记 filler{index}。",
            )
        con.commit()
    finally:
        con.close()
    rag.rebuild_index(CATEGORY, db_path=db_path)
    return db_path, ids


def _label(con, article_id: int, label: str, tier: str, score: float | None) -> None:
    con.execute(
        "INSERT OR REPLACE INTO viral_labels(article_id, label, confidence_tier, score, reason,"
        " features_json, labeled_at) VALUES(?, ?, ?, ?, '测试构造样本', '{}', ?)",
        (article_id, label, tier, score, NOW),
    )


def _performance(con, category: str, keyword: str, score: float | None, collected: int, quality: int,
                 origin: str = "test") -> None:
    cur = con.execute(
        "INSERT INTO keywords(keyword, category, origin, status, created_at)"
        " VALUES(?, ?, ?, 'active', ?)",
        (keyword, category, origin, NOW),
    )
    con.execute(
        "INSERT OR REPLACE INTO keyword_performance(keyword_id, collected, quality_articles,"
        " avg_text_length, score, last_used_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
        (int(cur.lastrowid), collected, quality, 900.0, score, NOW, NOW),
    )


def _counts(db_path: Path) -> dict[str, int]:
    """全表行数指纹:排题只读 ⇒ 任何业务表行数都不许变。"""
    con = sqlite3.connect(db_path)
    try:
        tables = [row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        return {
            table: int(con.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0])
            for table in tables
            if not table.startswith("sqlite_") and "fts" not in table
        }
    finally:
        con.close()


def test_supported_topic_passes_with_the_writing_gate(tmp_path):
    """6 篇相关全文 + 3 独立域名 ⇒ 放行;阈值必须来自配置,不是本地硬编码。"""
    db_path, _ = _corpus(tmp_path)
    support = topics.corpus_support(CATEGORY, HOT_TOPIC, db_path)
    assert support["thresholds"] == {"min_full": 6, "min_sources": 3}
    assert support["critical"] == [], support["critical"]
    assert support["gate"] == "pass"
    assert support["kept_full"] == 6
    assert support["distinct_sources"] == 3
    assert support["searched"] == 6, "冷主题凑数稿不得被召回"
    # pool_limit 是 rag.search **实际返回**的候选数(上限才是 MATERIAL_POOL_LIMIT)
    assert support["pool_limit"] == 6 and support["pool_limit"] <= workflow.MATERIAL_POOL_LIMIT


def test_gate_verdict_is_identical_to_write_article(tmp_path):
    """复用同一个实现:corpus_support 的 critical 与 _material_gate 逐字相同。"""
    db_path, _ = _corpus(tmp_path, hot=4, hot_hosts=("h0.test", "h1.test"))
    matches = rag.search(CATEGORY, HOT_TOPIC, limit=workflow.MATERIAL_POOL_LIMIT, db_path=db_path)
    _materials, stats = workflow._rank_materials(matches, db_path, topic=HOT_TOPIC)
    direct = workflow._material_gate(stats, 6, 3)
    support = topics.corpus_support(CATEGORY, HOT_TOPIC, db_path)
    assert support["critical"] == direct
    assert support["gate"] == "block"
    assert any("取材密度" in item for item in support["critical"])
    assert any("来源独立性" in item for item in support["critical"])


def test_topic_without_corpus_is_blocked_and_numbered(tmp_path):
    """库里没这个主题:直接拦下,并把 searched/kept 数字摊开(不是黑箱分数)。"""
    db_path, _ = _corpus(tmp_path)
    support = topics.corpus_support(CATEGORY, COLD_TOPIC, db_path)
    assert support["gate"] == "block"
    assert support["searched"] == 0
    assert support["kept"] == 0 and support["kept_full"] == 0
    assert support["distinct_sources"] == 0


def test_off_topic_recall_does_not_fake_support(tmp_path):
    """FTS 召回离题稿(R64-1 现场):不算支持度 ⇒ 拦下并写明「语料缺该主题素材」。"""
    db_path, _ = _corpus(tmp_path, hot=0, drift=4, cold_fillers=0)
    matches = rag.search(CATEGORY, DRIFT_TOPIC, limit=workflow.MATERIAL_POOL_LIMIT, db_path=db_path)
    assert matches, "前提:FTS 确实能召回这些稿子"
    support = topics.corpus_support(CATEGORY, DRIFT_TOPIC, db_path)
    assert support["searched"] >= 1
    assert support["kept"] == 0, "离题稿不得进素材池"
    assert support["dropped_off_topic"] >= 1
    assert any("语料缺该主题素材" in item for item in support["critical"]), support["critical"]


def test_keyword_scoreboard_becomes_candidate_pool(tmp_path):
    """关键词战绩是候选来源,score 降序 + origin 溯源;缺战绩如实报空并给下一步,不虚构加分。"""
    db_path, _ = _corpus(tmp_path)
    con = sqlite3.connect(db_path)
    try:
        _performance(con, CATEGORY, HOT_TOPIC, 0.12, 30, 20, origin="mined")  # 系统从爆款标题挖的词
        _performance(con, CATEGORY, COLD_TOPIC, 0.99, 5, 1, origin="seed")    # 人给的种子词
        _performance(con, CATEGORY, "无战绩主题", None, 0, 0)
        con.commit()
    finally:
        con.close()
    board = topics.keyword_candidates(CATEGORY, db_path, limit=10)
    assert board["status"] == "ok"
    assert [item["topic"] for item in board["items"]] == [COLD_TOPIC, HOT_TOPIC, "无战绩主题"]
    assert board["items"][0]["score"] == pytest.approx(0.99)
    # origin 必须带出来:能审计「这主题是系统学的还是人塞的」
    assert [item["origin"] for item in board["items"]] == ["seed", "mined", "test"]
    assert board["origins"] == ["mined", "seed", "test"]

    empty_db, _ = _corpus(tmp_path / "empty")
    empty_board = topics.keyword_candidates(CATEGORY, empty_db, limit=10)
    assert empty_board["status"] == "no_performance_rows"
    assert empty_board["items"] == []
    assert empty_board["note"], "缺战绩必须给出下一步动作,不能静默空榜"


def test_writable_topic_outranks_higher_scoring_but_unsupported(tmp_path):
    """排序规则先声明:放行优先于战绩分 —— 高分但写不出的题排在可写题之后。"""
    db_path, _ = _corpus(tmp_path)
    con = sqlite3.connect(db_path)
    try:
        _performance(con, CATEGORY, HOT_TOPIC, 0.10, 30, 20)  # 能写,但战绩分低
        _performance(con, CATEGORY, COLD_TOPIC, 0.99, 5, 1)  # 战绩分最高,语料却没素材
        con.commit()
    finally:
        con.close()
    bundle = topics.rank_topics(CATEGORY, db_path, limit=5, pool=10)
    assert bundle["counters"] == {"candidates": 2, "writable": 1, "blocked": 1,
                                  "viral_reference_titles": 0}
    assert bundle["ranking"][0]["topic"] == HOT_TOPIC
    assert bundle["ranking"][0]["support"]["gate"] == "pass"
    assert bundle["writable_topics"] == [HOT_TOPIC]
    blocked = bundle["blocked_topics"][0]
    assert blocked["topic"] == COLD_TOPIC
    hint = blocked["collection_hints"][0]["command"]
    assert hint.startswith(f"ttxt crawl-plan --category {CATEGORY}")
    assert COLD_TOPIC in hint, "补采集建议必须带上缺素材的那个主题词"


def test_measured_viral_titles_are_references_and_garbage_is_excluded(tmp_path):
    """爆款标题只作参考线索:measured 档 + verified/potential 标签;垃圾/中性标签逐个数并排除。"""
    db_path, ids = _corpus(tmp_path, hot=3)
    con = sqlite3.connect(db_path)
    try:
        _label(con, ids["hot0"], viral.LABEL_VERIFIED, viral.TIER_MEASURED, 0.62)
        _label(con, ids["hot1"], viral.LABEL_POTENTIAL, viral.TIER_MEASURED_READS, 0.31)
        _label(con, ids["filler0"], viral.LABEL_LOW, viral.TIER_MEASURED, 0.01)
        _label(con, ids["filler1"], viral.LABEL_NORMAL, viral.TIER_MEASURED, 0.05)
        con.commit()
    finally:
        con.close()
    refs = topics.viral_reference_titles(CATEGORY, db_path, limit=10)
    assert refs["status"] == "ok"
    assert refs["used_for"] == "reference_only_not_topic"
    assert refs["tiers"] == [viral.TIER_MEASURED, viral.TIER_MEASURED_READS]
    assert refs["reference_labels"] == [viral.LABEL_VERIFIED, viral.LABEL_POTENTIAL]
    assert [item["title"] for item in refs["items"]] == [
        "开源模型训练成本怎么降 观察0",
        "开源模型训练成本怎么降 观察1",
    ]
    assert refs["excluded_by_label"] == {viral.LABEL_NORMAL: 1, viral.LABEL_LOW: 1}
    assert all(item["label"] in topics.VIRAL_REF_LABELS for item in refs["items"])
    assert all(item["confidence_tier"] in topics.VIRAL_TIERS for item in refs["items"])
    # 参考线索必须能追到原文(不然只是句漂亮话)
    assert all(item["article_id"] and item["url"] for item in refs["items"])


def test_unmeasured_viral_tiers_are_not_evidence(tmp_path):
    """没有真实测得数据的标签(如 no_impressions)不得当爆款证据 —— 与 calibrate 同一立场。"""
    db_path, ids = _corpus(tmp_path, hot=2)
    con = sqlite3.connect(db_path)
    try:
        _label(con, ids["hot0"], viral.LABEL_VERIFIED, viral.TIER_NO_IMPRESSIONS, 0.90)
        _label(con, ids["hot1"], viral.LABEL_POTENTIAL, viral.TIER_INCOMPLETE, 0.80)
        con.commit()
    finally:
        con.close()
    refs = topics.viral_reference_titles(CATEGORY, db_path, limit=10)
    assert refs["status"] == "no_viral_reference_titles"
    assert refs["items"] == []
    assert refs["scanned"] == 0, "非测得档根本不进扫描窗口"


def test_awkward_viral_titles_are_counted_not_dropped_silently(tmp_path):
    """太短/太长的标题不成参考线索:跳过必须计数,不能静默丢。"""
    db_path, _ = _corpus(tmp_path, hot=0, cold_fillers=0)
    con = sqlite3.connect(db_path)
    try:
        long_id = _insert(con, CATEGORY, "long", "g.test",
                          "开源模型训练成本下降的行业观察补充说明" * 4, HOT_SENTENCE * 30)
        short_id = _insert(con, CATEGORY, "short", "s.test", "成本", HOT_SENTENCE * 30)
        ok_id = _insert(con, CATEGORY, "ok", "o.test", "开源模型训练成本怎么降", HOT_SENTENCE * 30)
        for article_id in (long_id, short_id, ok_id):
            _label(con, article_id, viral.LABEL_VERIFIED, viral.TIER_MEASURED, 0.6)
        con.commit()
    finally:
        con.close()
    refs = topics.viral_reference_titles(CATEGORY, db_path, limit=10)
    assert [item["title"] for item in refs["items"]] == ["开源模型训练成本怎么降"]
    assert refs["skipped"]["too_long"] == 1
    assert refs["skipped"]["too_short"] == 1
    assert refs["skipped"]["scanned"] == 3


def test_whole_viral_title_stays_out_of_the_topic_ranking(tmp_path):
    """生产实测缺陷的回归(2026-11-21 social):整条爆款标题当主题 ⇒ 只召回自己、必被拦。
    所以标题不进候选池,只当参考;支持度尺子只用在词/短语级主题上。"""
    db_path, _ = _corpus(tmp_path, hot=0, cold_fillers=0)
    con = sqlite3.connect(db_path)
    try:
        title = "三十一岁程序员失业后靠副业翻身实录"
        # 正文与标题同题(生产那 10 条的形状:相关全文 1、独立来源 1 —— 撑不起 6/3 门禁)
        viral_id = _insert(con, CATEGORY, "viral", "v.test", title, "三十一岁程序员失业,靠副业翻身。" * 30)
        _label(con, viral_id, viral.LABEL_VERIFIED, viral.TIER_MEASURED, 0.91)
        con.commit()
    finally:
        con.close()
    rag.rebuild_index(CATEGORY, db_path=db_path)
    assert rag.search(CATEGORY, title, limit=workflow.MATERIAL_POOL_LIMIT, db_path=db_path), \
        "前提:整条标题在 FTS 里召得回自己"
    support = topics.corpus_support(CATEGORY, title, db_path)
    assert support["kept_full"] == 1 and support["distinct_sources"] == 1
    assert support["gate"] == "block", "整标题撑不起 6/3 门禁 —— 就是生产里那 10 条被拦候选"
    bundle = topics.rank_topics(CATEGORY, db_path, limit=5, pool=10)
    assert bundle["counters"]["candidates"] == 0, "整条标题不得混进主题榜"
    assert [item["title"] for item in bundle["viral_reference_titles"]["items"]] == [title]
    assert "不参与支持度排序" in bundle["viral_reference_titles"]["note"]
    assert "mine_keywords" in bundle["viral_reference_titles"]["note"], "得指出去哪把爆款变成词"
    assert bundle["method"]["title_seeds"].startswith("disabled")


def test_no_candidates_reports_empty_instead_of_inventing(tmp_path):
    """有关键词/标签表但没数据:状态如实报,候选为 0,不抛异常也不编主题。"""
    db_path, _ = _corpus(tmp_path)
    bundle = topics.rank_topics(CATEGORY, db_path, limit=5, pool=10)
    assert bundle["counters"] == {"candidates": 0, "writable": 0, "blocked": 0,
                                  "viral_reference_titles": 0}
    assert bundle["candidate_sources"]["keyword_performance"]["status"] == "no_performance_rows"
    assert bundle["viral_reference_titles"]["status"] == "no_viral_reference_titles"
    assert bundle["counters"]["viral_reference_titles"] == 0
    assert bundle["writable_topics"] == []


def test_extra_topics_share_the_same_ruler_and_dedupe(tmp_path):
    """人工补题(--topic)走同一把尺子;同一主题去重,来源并列保留。"""
    db_path, _ = _corpus(tmp_path)
    bundle = topics.rank_topics(
        CATEGORY, db_path, limit=5, pool=5, extra_topics=[HOT_TOPIC, f"  {HOT_TOPIC} ", COLD_TOPIC]
    )
    assert [item["topic"] for item in bundle["ranking"]] == [HOT_TOPIC, COLD_TOPIC]
    assert [src["kind"] for src in bundle["ranking"][0]["sources"]] == [topics.CLI_SOURCE]
    assert bundle["ranking"][0]["support"]["gate"] == "pass"
    assert bundle["ranking"][1]["support"]["gate"] == "block"


def test_ranking_is_read_only(tmp_path):
    """只读红线:排题不写业务表(全表行数指纹前后一致),也不产草稿。"""
    db_path, ids = _corpus(tmp_path, hot=6)
    con = sqlite3.connect(db_path)
    try:
        _performance(con, CATEGORY, HOT_TOPIC, 0.30, 30, 20)
        _performance(con, CATEGORY, COLD_TOPIC, 0.55, 9, 3)
        _label(con, ids["hot0"], viral.LABEL_VERIFIED, viral.TIER_MEASURED, 0.62)
        con.commit()
    finally:
        con.close()
    before = _counts(db_path)
    assert before["writing_history"] == 0
    bundle = topics.rank_topics(CATEGORY, db_path, limit=5, pool=10)
    assert bundle["counters"]["candidates"] == 2
    assert bundle["counters"]["viral_reference_titles"] == 1, "参考线索只读不写"
    after = _counts(db_path)
    assert after == before, {"before": before, "after": after}
    assert after["writing_history"] == 0


def test_evidence_file_is_auditable_and_declares_reuse_only(tmp_path):
    """证据文件必须能审计:方法声明复用、阈值来自配置、被拦主题带采集建议。"""
    db_path, _ = _corpus(tmp_path)
    con = sqlite3.connect(db_path)
    try:
        _performance(con, CATEGORY, COLD_TOPIC, 0.99, 5, 1)
        con.commit()
    finally:
        con.close()
    bundle = topics.rank_topics(CATEGORY, db_path, limit=5, pool=10, extra_topics=[HOT_TOPIC])
    writing = cfg.load_config()["writing"]
    path = topics.write_evidence(bundle, topics.evidence_path(CATEGORY, bundle["generated_at"], tmp_path))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["kind"] == "topic_support_ranking"
    assert data["method"]["reuse_only"] is True
    assert "workflow._material_gate" in data["method"]["chain"]
    assert data["method"]["thresholds"] == {
        "min_full": int(writing["material_min_full"]),
        "min_sources": int(writing["material_min_sources"]),
    }
    assert data["counters"]["writable"] == 1 and data["counters"]["blocked"] == 1
    assert data["blocked_topics"][0]["topic"] == COLD_TOPIC
    assert "crawl-plan" in data["blocked_topics"][0]["collection_hints"][0]["command"]
    # 标题种子关掉 + 参考线索的口径都写进证据文件:别人复查时不用猜
    assert data["method"]["title_seeds"].startswith("disabled")
    assert data["method"]["sources"] == [topics.KEYWORD_SOURCE]
    assert data["viral_reference_titles"]["used_for"] == "reference_only_not_topic"


def test_cli_exits_nonzero_and_says_fix_the_corpus_first(tmp_path, capsys):
    """CLI:无可写主题 ⇒ 明确「先补采集、不放宽门禁」并返回非 0(脚本可判断)。"""
    db_path, _ = _corpus(tmp_path, hot=0, cold_fillers=3)
    args = argparse.Namespace(
        category=CATEGORY, db_path=str(db_path), limit=5, pool=10, topic=[COLD_TOPIC],
        sources="keyword_performance", no_refs=False, out=str(tmp_path / "evidence"), no_out=False,
    )
    assert cmd_topics(args) == 3
    out = capsys.readouterr().out
    assert "没有语料支撑得起的主题" in out
    assert "先补采集" in out
    assert "ttxt crawl-plan" in out
    assert list((tmp_path / "evidence").glob("*.json"))
    assert _counts(db_path)["writing_history"] == 0


def test_cli_exits_zero_when_a_topic_is_writable(tmp_path, capsys):
    """CLI:有可写主题 ⇒ 返回 0,并把支持度数字摊在行首(可核对,不是黑箱)。"""
    db_path, ids = _corpus(tmp_path, hot=6)
    con = sqlite3.connect(db_path)
    try:
        _label(con, ids["hot0"], viral.LABEL_VERIFIED, viral.TIER_MEASURED, 0.62)
        con.commit()
    finally:
        con.close()
    args = argparse.Namespace(
        category=CATEGORY, db_path=str(db_path), limit=5, pool=10, topic=[HOT_TOPIC],
        sources="keyword_performance", no_refs=False, out=str(tmp_path / "evidence"), no_out=True,
    )
    assert cmd_topics(args) == 0
    out = capsys.readouterr().out
    assert "[放行]" in out
    assert "相关全文 6/6" in out
    assert "独立来源 3" in out
    assert "参考爆款标题 1 条(不参与支持度排序)" in out
    assert not (tmp_path / "evidence").exists(), "--no-out 时不得落证据文件"


def test_cli_no_refs_skips_the_reference_query(tmp_path, capsys):
    """--no-refs ⇒ 不列参考标题,证据字段为 null(少一次只读查询,口径仍写清楚)。"""
    db_path, ids = _corpus(tmp_path, hot=6)
    con = sqlite3.connect(db_path)
    try:
        _label(con, ids["hot0"], viral.LABEL_VERIFIED, viral.TIER_MEASURED, 0.62)
        con.commit()
    finally:
        con.close()
    args = argparse.Namespace(
        category=CATEGORY, db_path=str(db_path), limit=5, pool=10, topic=[HOT_TOPIC],
        sources="keyword_performance", no_refs=True, out=str(tmp_path / "evidence"), no_out=False,
    )
    assert cmd_topics(args) == 0
    assert "参考爆款标题" not in capsys.readouterr().out
    data = json.loads(next((tmp_path / "evidence").glob("*.json")).read_text(encoding="utf-8"))
    assert data["viral_reference_titles"] is None


def test_wrong_source_name_is_named_not_read_as_empty_corpus(tmp_path, capsys):
    """--sources 写错(或把爆款标题当来源):候选 0 要点名来源,不能被读成「库里没主题」。"""
    db_path, _ = _corpus(tmp_path, cold_fillers=0)
    ok = topics.rank_topics(CATEGORY, db_path, limit=5, pool=10)
    assert ok["method"]["unknown_sources"] == []
    assert ok["method"]["requested_sources"] == [topics.KEYWORD_SOURCE]

    wrong = topics.rank_topics(
        CATEGORY, db_path, limit=5, pool=10, sources=("viral_titles", topics.REF_SOURCE)
    )
    assert wrong["counters"]["candidates"] == 0
    assert wrong["candidate_sources"] == {}
    assert wrong["method"]["unknown_sources"] == ["viral_titles", topics.REF_SOURCE]

    args = argparse.Namespace(
        category=CATEGORY, db_path=str(db_path), limit=5, pool=10, topic=[],
        sources="viral_titles,viral_reference_titles", no_refs=True,
        out=str(tmp_path / "evidence"), no_out=False,
    )
    assert cmd_topics(args) == 3
    out = capsys.readouterr().out
    assert "未知候选来源" in out and "viral_titles" in out
    assert "命令行写错,不是「库里没有主题」" in out
    assert "没有语料支撑得起的主题" not in out
    data = json.loads(next((tmp_path / "evidence").glob("*.json")).read_text(encoding="utf-8"))
    assert data["method"]["unknown_sources"] == ["viral_titles", topics.REF_SOURCE]
