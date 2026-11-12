"""P4 测试:FTS5 RAG、关键词评分、爆款标签(缺数据/有指标)、知识资产、类目隔离。"""

import sqlite3

from ttxt import db
from ttxt.analysis import analyze, keywords, rag, viral
from ttxt.crawler import store
from ttxt.crawler.extract import ExtractedItem


def _item(title: str, content: str, url: str) -> ExtractedItem:
    return ExtractedItem(
        title=title,
        content=content,
        excerpt="",
        author="",
        published_at=None,
        url=url,
    )


def _prepared_db(tmp_path):
    db_path = store.init_articles_db("demo", db_path=tmp_path / "demo.db")
    store.insert_items(
        "demo",
        "test",
        [
            _item("人工智能与开源", "人工智能正在改变世界,开源模型让研究更高效。", "https://example.com/1"),
            _item("城市漫步", "城市漫步指南,适合周末出门。", "https://example.com/2"),
        ],
        db_path=db_path,
    )
    return db_path


def test_rag_search(tmp_path):
    db_path = _prepared_db(tmp_path)
    store.migrate_articles_db("demo", db_path=db_path)
    stats = rag.rebuild_index("demo", db_path=db_path)
    assert stats["indexed"] == 2
    hits = rag.search("demo", "人工智能", db_path=db_path)
    assert hits
    assert hits[0]["title"] == "人工智能与开源"
    assert rag.search("demo", "", db_path=db_path) == []


def test_rag_search_or_fallback(tmp_path):
    db_path = _prepared_db(tmp_path)
    store.migrate_articles_db("demo", db_path=db_path)
    rag.rebuild_index("demo", db_path=db_path)
    # 任一篇文章都无法同时包含全部双字词 → AND 失败,长查询回退 OR 仍能召回
    hits = rag.search("demo", "人工智能 城市", db_path=db_path)
    assert hits


def test_rag_search_strict_short_query(tmp_path):
    db_path = _prepared_db(tmp_path)
    store.migrate_articles_db("demo", db_path=db_path)
    rag.rebuild_index("demo", db_path=db_path)
    # 短查询不走 OR 回退,避免"智能"误命中"人工智能"查询
    assert rag.search("demo", "天气 晴朗", db_path=db_path) == []


def test_keyword_scoring(tmp_path):
    db_path = _prepared_db(tmp_path)
    store.migrate_articles_db("demo", db_path=db_path)
    keywords.seed_keywords("demo", db_path=db_path)
    stats = keywords.score_keywords("demo", db_path=db_path)
    assert stats["keywords"] >= 4
    assert "人工智能" not in stats["feedback_candidates"]
    assert "效率工具" in stats["feedback_candidates"]

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT k.keyword, p.collected FROM keywords k "
            "JOIN keyword_performance p ON p.keyword_id = k.id WHERE k.keyword='人工智能'"
        ).fetchone()
        assert row["collected"] == 1
    finally:
        con.close()


def test_viral_missing_data(tmp_path):
    db_path = _prepared_db(tmp_path)
    store.migrate_articles_db("demo", db_path=db_path)
    stats = viral.label_category("demo", db_path=db_path)
    assert stats["tiers"] == {"missing_data": 2}


def test_viral_with_metrics(tmp_path):
    db_path = _prepared_db(tmp_path)
    store.migrate_articles_db("demo", db_path=db_path)
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO article_metrics(article_id, reads, likes, favorites, comments, impressions, "
        "data_source, credibility, captured_at) VALUES (1, 3000, 200, 300, 50, 10000, 'authorized_export', 'verified', '2026-11-12')"
    )
    con.commit()
    con.close()
    stats = viral.label_category("demo", db_path=db_path)
    assert stats["tiers"].get("measured") == 1

    con = sqlite3.connect(db_path)
    try:
        label, tier, score = con.execute(
            "SELECT label, confidence_tier, score FROM viral_labels WHERE article_id=1"
        ).fetchone()
        assert label == "verified_viral"
        assert tier == "measured"
        assert score is not None
    finally:
        con.close()


def test_analyze_pipeline_idempotent(tmp_path):
    db_path = _prepared_db(tmp_path)
    db.init_system_db(tmp_path / "sys.db")
    kwargs = dict(db_path=db_path, system_db=tmp_path / "sys.db", evidence_dir=tmp_path / "feedback")
    first = analyze.run_analysis("demo", **kwargs)
    second = analyze.run_analysis("demo", **kwargs)
    assert first["viral"]["tiers"] == {"missing_data": 2}
    assert first["index"]["indexed"] == 2
    assert second["index"]["indexed"] == 2
    # P11 反哺:首轮把零覆盖关键词由 active 转 gap,计数随之下降;进入稳定态后幂等。
    assert second["keywords"]["keywords"] <= first["keywords"]["keywords"]
    third = analyze.run_analysis("demo", **kwargs)
    assert third["keywords"]["keywords"] == second["keywords"]["keywords"]
    # 演示类目没有正式来源:只记录反哺证据,绝不入队真实任务。
    assert first["feedback"]["queued_job_id"] is None
    assert first["feedback"]["gap_keywords"]

    con = sqlite3.connect(db_path)
    try:
        assert con.execute("SELECT COUNT(*) FROM knowledge_assets").fetchone()[0] >= 1
        assert con.execute("SELECT COUNT(*) FROM keyword_performance").fetchone()[0] >= 4
    finally:
        con.close()


def test_search_scope_isolation(tmp_path):
    db_one = store.init_articles_db("demo", db_path=tmp_path / "one.db")
    db_two = store.init_articles_db("other", db_path=tmp_path / "two.db")
    store.insert_items("demo", "t", [_item("人工智能文章", "人工智能内容。", "https://example.com/x")], db_path=db_one)
    store.insert_items("other", "t", [_item("财经文章", "股票市场内容。", "https://example.com/y")], db_path=db_two)
    store.migrate_articles_db("demo", db_path=db_one)
    store.migrate_articles_db("other", db_path=db_two)
    rag.rebuild_index("demo", db_path=db_one)
    rag.rebuild_index("other", db_path=db_two)
    assert rag.search("demo", "人工智能", db_path=db_one)
    assert rag.search("other", "人工智能", db_path=db_two) == []
