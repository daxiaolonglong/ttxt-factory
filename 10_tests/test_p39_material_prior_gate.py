# -*- coding: utf-8 -*-
"""P39(阶段1):写作取材必须带真实爆款先验,且取材密度不足就烧不到模型。

用户目标 2+3 的接线缺口(2026-11-17 实测):
- ``write_article`` 只用 ``rag.search(limit=5)`` 的 BM25 顺序,``viral_labels`` 里
  **真实测得**的爆款标签对"选哪 5 篇材料"毫无影响 ⇒ 采到的爆款数据喂不进写作;
- 14:36 那次三版全 rejected 的根因是**取材密度**:1 篇材料扛着「50 多个国家」的标题数字,
  引句绑不上。让模型先瞎写再被事实门禁拦,既烧 token 也留一堆 rejected 版本。

钉死的口径:
- 先验只认本类目库 ``confidence_tier='measured'`` 且 ``score`` 非空 的真实标签
  (与 ``keywords.score_keywords`` 同一套词表、同一 ``VIRAL_WEIGHT`` 权重);
- 类目没有 ``viral_calibration`` ⇒ 退化为纯 BM25 并如实写 ``calibration_status="uncalibrated"``,
  绝不虚构加分也不把缺数据当 0 分(规则 6);
- 证据密度 = ``completeness='full'`` 的行数 + 独立来源数(作者账号优先,缺作者退回域名并按
  去 ``www.`` 的域名计;生产语料实测每个类目只有一个域名,只按域名计会把所有写作请求
  一律拦下),门槛来自 ``writing.material_min_full / material_min_sources``;不达标直接返回
  ``status="insufficient_materials"`` 且**零模型调用**(哨兵模型被调用即红)。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ttxt import config as cfg
from ttxt.analysis import rag, viral
from ttxt.crawler import cleaner, store
from ttxt.writing import workflow

TOPIC = "人工智能与开源"
FULL_TAIL = "开源模型让研究更高效,社区协作把训练门槛降下来。" * 24  # ≥400 字 ⇒ full
NOW = "2026-11-18T09:00:00+08:00"


def _body(marker: str, hits: int) -> str:
    """hits 控制主题词重复次数 ⇒ BM25 可控;identical 语料里所有文章 hits 相同 ⇒ 分数完全相同。

    ``marker`` 只出现在正文中段,不参与主题词命中(标题也不带主题词,避免凑数文章混进候选池)。
    """
    return f"{TOPIC} 深度观察。" * hits + f"背景补充:{marker}。" + FULL_TAIL


def _insert(con, category: str, marker: str, host: str, title: str, text: str,
            author: str | None = None) -> int:
    url = f"https://{host}/a-{marker}"
    cur = con.execute(
        "INSERT INTO articles(category, source, title, content, cleaned_content, text_length,"
        " completeness, author, url, url_normalized, content_hash, fetched_at)"
        " VALUES(?, 'src', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            category,
            title,
            text,
            text,
            cleaner.usable_length(text),
            cleaner.completeness_of(text),
            author,
            url,
            url,
            f"hash-{category}-{marker}",
            NOW,
        ),
    )
    return int(cur.lastrowid)


def _corpus(
    tmp_path: Path,
    category: str = "demo",
    *,
    hosts: tuple[str, ...] = ("a.test", "b.test", "c.test", "d.test"),
    full: int = 6,
    short: int = 0,
    fillers: int = 0,
    identical: bool = False,
    authors: tuple[str, ...] | None = None,
) -> tuple[Path, dict]:
    """造类目库:full/short 可控、域名可控;fillers 是**不命中主题**的凑数文章(只喂标定样本)。

    ``identical=True`` 时所有命中文章正文完全相同 ⇒ BM25 分数完全相同,先验的作用可被单独观测。
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / f"{category}.db"
    store.migrate_articles_db(category, db_path=db_path)
    ids: dict[str, int] = {}
    con = sqlite3.connect(db_path)
    try:
        for index in range(full):
            marker = f"full{index}"
            hits = 1 if identical else full - index  # 递减:最后一篇 BM25 最差
            ids[marker] = _insert(
                con, category, marker, hosts[index % len(hosts)],
                f"观察 {marker}", _body("同一篇" if identical else marker, hits),
                author=authors[index % len(authors)] if authors else None,
            )
        for index in range(short):
            marker = f"short{index}"
            ids[marker] = _insert(
                con, category, marker, hosts[index % len(hosts)], f"简讯 {marker}",
                f"{TOPIC} 简讯。开源模型让研究更高效。",
            )
        for index in range(fillers):
            marker = f"filler{index}"
            ids[marker] = _insert(con, category, marker, "z.test", f"凑数文章 {index}",
                                  f"无关内容记录 {index} " + "行业观察随笔。" * 60)
        con.commit()
    finally:
        con.close()
    rag.rebuild_index(category, db_path=db_path)
    return db_path, ids


def _measured_labels(db_path: Path, article_ids: list[int], label: str, score: float) -> None:
    con = sqlite3.connect(db_path)
    try:
        for article_id in article_ids:
            con.execute(
                "INSERT OR REPLACE INTO viral_labels(article_id, label, confidence_tier, score, reason,"
                " features_json, labeled_at) VALUES(?, ?, 'measured', ?, '测试构造的真实测得样本', '{}', ?)",
                (article_id, label, score, NOW),
            )
        con.commit()
    finally:
        con.close()


def _rank(db_path: Path) -> tuple[list[dict], dict]:
    matches = rag.search("demo", TOPIC, limit=workflow.MATERIAL_POOL_LIMIT, db_path=db_path)
    return workflow._rank_materials(matches, db_path)


class BoomModel:
    """哨兵:被调用就说明门禁没拦住,断言当场红(比"断言 mock 没被调用"更难造假)。"""

    def __call__(self, *args, **kwargs):
        raise AssertionError("取材不足时不得调用模型")


class FakeModel:
    """最小可用模型替身:只用来证明"门禁放行后确实调到了模型"。"""

    def __init__(self) -> None:
        self.draft_calls = 0

    @staticmethod
    def _draft() -> str:
        return f"{TOPIC}\n开源模型让研究更高效,这是一个完整的结束句。"

    def __call__(self, prompt, max_tokens=800, temperature=0.3, enable_thinking=False):
        if "请为主题准备写作大纲" in prompt:
            return "- 背景\n- 结语"
        if "关键事实陈述" in prompt:
            return '[{"claim": "开源模型让研究更高效", "article_id": 1}]'
        if "严格的中文内容评审" in prompt:
            return '{"score": 0.8, "issues": [], "critical": []}'
        self.draft_calls += 1
        return self._draft()


def test_uncalibrated_category_falls_back_to_pure_bm25(tmp_path):
    """没有 viral_calibration ⇒ 爆款项一律不参与排序,顺序必须等于纯 BM25,并如实标注。"""
    db_path, ids = _corpus(tmp_path, full=8)
    _measured_labels(db_path, [ids["full7"]], "verified_viral", 0.42)
    materials, stats = _rank(db_path)

    matches = rag.search("demo", TOPIC, limit=workflow.MATERIAL_POOL_LIMIT, db_path=db_path)
    bm25_top5 = [match["article_id"] for match in matches[: workflow.MATERIAL_TOP_K]]
    assert stats["pool_limit"] == 8
    assert stats["candidates"] == 8
    assert stats["calibration_status"] == "uncalibrated"
    assert stats["priors"]["applied"] is False
    assert stats["priors"]["reason"]
    assert len(materials) == workflow.MATERIAL_TOP_K
    assert stats["selected_ids"] == bm25_top5
    assert all(entry["viral_prior"] is None for entry in stats["scores"]), "未标定类目不得有爆款项"
    assert ids["full7"] not in stats["selected_ids"], "未标定时真爆款标签不得偷偷抬升选材"


def test_measured_viral_prior_changes_which_articles_are_used(tmp_path):
    """同一份语料、BM25 完全相同的六篇:标定后真爆款那篇必须换掉一篇普通材料(先验可观测)。"""
    db_path, ids = _corpus(tmp_path, full=6, fillers=30, identical=True)
    hot_id = ids["full5"]  # 池内排最后 ⇒ 纯 BM25 下进不了前 5
    _measured_labels(db_path, [hot_id], "verified_viral", 0.42)
    _measured_labels(db_path, [ids[f"filler{i}"] for i in range(30)], "normal", 0.04)

    before, stats_before = _rank(db_path)
    assert stats_before["calibration_status"] == "uncalibrated"
    assert hot_id not in stats_before["selected_ids"]

    outcome = viral.calibrate("demo", db_path=db_path)
    assert outcome["status"] not in ("insufficient_sample", "degenerate_distribution"), outcome

    materials, stats = _rank(db_path)
    scored = {entry["id"]: entry for entry in stats["scores"]}
    assert stats["calibration_status"] == "calibrated"
    assert stats["priors"]["applied"] is True
    assert stats["priors"]["weight"] == pytest.approx(0.30)
    assert stats["priors"]["measured_label_hits"] == 1
    assert stats["priors"]["label_counts"] == {"verified_viral": 1}
    assert hot_id in stats["selected_ids"], "真实爆款先验必须改变取材选择"
    assert scored[hot_id]["viral_prior"] == pytest.approx(1.0)
    assert scored[hot_id]["rank_score"] > max(
        entry["rank_score"] for entry in stats["scores"] if entry["id"] != hot_id
    )
    assert {item["id"] for item in materials} == set(stats["selected_ids"])


def test_full_articles_are_preferred_over_short_ones(tmp_path):
    """证据密度:6 全文 + 2 短稿 ⇒ 选材 5 篇必须全是全文(短稿撑不起引句绑定)。"""
    db_path, _ = _corpus(tmp_path, full=6, short=2)
    _materials, stats = _rank(db_path)
    assert stats["full_candidates"] == 6
    assert stats["selected_full"] == workflow.MATERIAL_TOP_K
    assert stats["candidates"] == 8


def test_www_prefix_does_not_count_as_a_second_source(tmp_path):
    """www.a.test 与 a.test 是同一站点,不得被算成两个独立来源(证据密度会虚高)。"""
    db_path, _ = _corpus(tmp_path, hosts=("www.a.test", "a.test"), full=6)
    _materials, stats = _rank(db_path)
    assert stats["distinct_sources"] == 1
    assert stats["distinct_hosts"] == 1


def test_same_platform_different_authors_are_independent_sources(tmp_path):
    """生产实测口径:正式类目全是 toutiao.com ⇒ 独立性只能按作者账号计。

    6 篇同一域名、6 个不同作者:独立来源必须是 6 个(域名轴只有 1 个),门禁放行。
    """
    db_path, _ = _corpus(tmp_path, hosts=("toutiao.test",), full=6,
                         authors=tuple(f"作者{index}" for index in range(6)))
    _materials, stats = _rank(db_path)
    assert stats["distinct_hosts"] == 1
    assert stats["distinct_sources"] == 6
    assert stats["independence_axis"]["articles_counted_by_author"] == 6
    assert stats["independence_axis"]["articles_counted_by_host"] == 0
    assert workflow._material_gate(stats, 6, 3) == []


def test_one_author_copies_is_not_independent_even_with_many_articles(tmp_path):
    """同一作者在同一站点发 6 篇:独立来源只有 1 个 ⇒ 门禁必须拦(单源改写风险)。"""
    db_path, _ = _corpus(tmp_path, hosts=("toutiao.test",), full=6, authors=("同一个作者",))
    _materials, stats = _rank(db_path)
    assert stats["candidates"] == 6
    assert stats["full_candidates"] == 6
    assert stats["distinct_sources"] == 1
    critical = workflow._material_gate(stats, 6, 3)
    assert len(critical) == 1 and "来源独立性" in critical[0], critical
    assert "作者" in critical[0], "回执必须写清独立性按哪个轴计"


def test_missing_author_falls_back_to_host_and_never_counts_as_extra(tmp_path):
    """缺作者的行退回域名轴;作者与域名不会叠加计数(同一作者+同一域名只算 1 个来源)。"""
    db_path, _ = _corpus(tmp_path, hosts=("a.test", "b.test"), full=4, authors=None)
    _materials, stats = _rank(db_path)
    assert stats["independence_axis"]["articles_counted_by_author"] == 0
    assert stats["independence_axis"]["articles_counted_by_host"] == 4
    assert stats["distinct_sources"] == stats["distinct_hosts"] == 2


def test_gate_blocks_thin_materials_with_zero_model_calls(tmp_path):
    """1 篇材料(生产 14:36 的现场条件):门禁直接拦下,哨兵模型一次都没被调用。"""
    db_path, _ = _corpus(tmp_path, hosts=("a.test",), full=1)
    export_dir = tmp_path / "drafts"
    result = workflow.write_article(
        "demo",
        TOPIC,
        model_call=BoomModel(),
        db_path=db_path,
        export_dir=export_dir,
    )
    assert result["status"] == "insufficient_materials"
    assert result["passed"] is False
    assert result["model_calls"] == 0
    assert len(result["critical"]) == 2, result["critical"]
    assert any("取材密度" in item for item in result["critical"])
    assert any("来源独立性" in item for item in result["critical"])
    assert result["material_stats"]["full_candidates"] == 1
    assert result["material_stats"]["distinct_sources"] == 1
    assert not export_dir.exists(), "取材门禁拦下时不得产出任何草稿文件"
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("SELECT COUNT(*) FROM writing_history").fetchone()[0]
    finally:
        con.close()
    assert rows == 0, "取材门禁拦下时不得往 writing_history 写版本"


def test_gate_threshold_matrix(tmp_path):
    """门槛逐维可核:6 全文 + 3 独立域名刚好放行;任一维差一点,只报那一维。"""
    cases = [
        ("pass", 6, 3, [], ["取材密度", "来源独立性"]),
        ("few_full", 5, 4, ["取材密度"], ["来源独立性"]),
        ("few_hosts", 6, 2, ["来源独立性"], ["取材密度"]),
    ]
    for name, full, host_count, want, forbid in cases:
        db_path, _ = _corpus(tmp_path / name, hosts=tuple(f"h{i}.test" for i in range(host_count)), full=full)
        _materials, stats = _rank(db_path)
        critical = workflow._material_gate(stats, 6, 3)
        for word in want:
            assert any(word in item for item in critical), (name, stats, critical)
        for word in forbid:
            assert not any(word in item for item in critical), (name, stats, critical)


def test_thresholds_come_from_config_writing_section(tmp_path, monkeypatch):
    """门槛不许硬编码在代码里:改配置就改门禁(生产调取材密度只动配置)。"""
    monkeypatch.setattr(
        cfg, "load_config", lambda: {"writing": {"material_min_full": 1, "material_min_sources": 1}}
    )
    db_path, _ = _corpus(tmp_path, hosts=("a.test",), full=1)
    fake = FakeModel()
    result = workflow.write_article(
        "demo", TOPIC, model_call=fake, db_path=db_path, export_dir=tmp_path / "drafts"
    )
    assert result["status"] != "insufficient_materials"
    assert fake.draft_calls >= 1, "门槛放行后必须真的调用模型"
    assert result["material_stats"]["full_candidates"] == 1


def test_production_defaults_are_six_full_and_three_domains():
    """写进配置的默认门槛 = 计划定死的 6 全文 / 3 独立域名(缺一项就红)。"""
    writing = cfg.load_config().get("writing", {})
    assert int(writing["material_min_full"]) == 6
    assert int(writing["material_min_sources"]) == 3
