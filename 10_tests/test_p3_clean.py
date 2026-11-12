"""P3 测试:时间归一化、清洗、simhash、迁移幂等、聚类与类目隔离。"""

import sqlite3

from ttxt.crawler import cleaner, pipeline, simhash, store, timeparse
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


def test_timeparse_variants():
    assert timeparse.normalize_datetime("2026-11-12T08:30:00+08:00") == "2026-11-12T08:30:00+08:00"
    assert timeparse.normalize_datetime("Tue, 11 Nov 2025 08:00:00 +0800").startswith("2025-11-11T08:00:00")
    assert timeparse.normalize_datetime("2026年11月12日 08:30").startswith("2026-11-12T08:30:00")
    assert timeparse.normalize_datetime("11月12日 08:30") is None
    assert timeparse.normalize_datetime(None) is None


def test_cleaner_removes_noise_lines():
    raw = "这是正文第一段。\n上一篇：某文章\n责任编辑：张三\n关注公众号\n这是正文第二段。"
    cleaned = cleaner.clean_article(raw)
    assert "上一篇" not in cleaned
    assert "责任编辑" not in cleaned
    assert "关注公众号" not in cleaned
    assert "正文第一段" in cleaned
    assert "正文第二段" in cleaned


def test_simhash_near_duplicate():
    a = "人工智能正在改变世界,机器学习模型需要大量数据来训练和优化。"
    b = "人工智能正在改变世界,机器学习模型需要大量数据来训练和优化!"
    c = "数据库索引可以显著加速查询,尤其是在大数据量场景下。"
    hash_a = simhash.simhash64(a)
    hash_b = simhash.simhash64(b)
    hash_c = simhash.simhash64(c)
    assert simhash.hamming(hash_a, hash_b) <= 3
    assert simhash.hamming(hash_a, hash_c) > 10


def test_migrate_idempotent(tmp_path):
    db_path = tmp_path / "a.db"
    store.init_articles_db("demo", db_path=db_path)
    store.migrate_articles_db("demo", db_path=db_path)
    store.migrate_articles_db("demo", db_path=db_path)
    con = sqlite3.connect(db_path)
    try:
        columns = [row[1] for row in con.execute("PRAGMA table_info(articles)")]
        assert columns.count("simhash") == 1
        assert columns.count("cleaned_content") == 1
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "article_clusters" in tables
        version = con.execute("SELECT value FROM articles_meta WHERE key='schema_version'").fetchone()[0]
        assert version == store.SCHEMA_VERSION
    finally:
        con.close()


def test_pipeline_clusters(tmp_path):
    db_path = store.init_articles_db("demo", db_path=tmp_path / "demo.db")
    items = [
        _item("AI", "人工智能正在改变世界,机器学习模型需要大量数据来训练和优化。", "https://example.com/a"),
        _item("AI2", "人工智能正在改变世界,机器学习模型需要大量数据来训练和优化!", "https://example.com/a2"),
        _item("DB", "数据库索引可以显著加速查询,尤其是在大数据量场景下。", "https://example.com/b"),
    ]
    store.insert_items("demo", "test", items, db_path=db_path)
    stats = pipeline.run_pipeline("demo", db_path=db_path)
    assert stats["articles"] == 3
    assert stats["clusters"] == 2
    assert stats["near_duplicate_pairs"] >= 1

    con = sqlite3.connect(db_path)
    try:
        rows = {row[0]: row[1] for row in con.execute("SELECT title, cluster_id FROM articles")}
        assert rows["AI"] == rows["AI2"]
        assert rows["DB"] != rows["AI"]
    finally:
        con.close()

    rerun = pipeline.run_pipeline("demo", db_path=db_path)
    assert rerun["clusters"] == 2


def test_category_isolation(tmp_path):
    db_one = store.init_articles_db("demo", db_path=tmp_path / "one.db")
    db_two = store.init_articles_db("other", db_path=tmp_path / "two.db")
    store.insert_items("demo", "t", [_item("X", "唯一内容一,关于科技。", "https://example.com/x")], db_path=db_one)
    store.insert_items("other", "t", [_item("Y", "唯一内容二,关于财经。", "https://example.com/y")], db_path=db_two)
    pipeline.run_pipeline("demo", db_path=db_one)
    pipeline.run_pipeline("other", db_path=db_two)

    con_one = sqlite3.connect(db_one)
    con_two = sqlite3.connect(db_two)
    try:
        titles_one = [row[0] for row in con_one.execute("SELECT title FROM articles")]
        titles_two = [row[0] for row in con_two.execute("SELECT title FROM articles")]
        assert titles_one == ["X"]
        assert titles_two == ["Y"]
        assert con_one.execute("SELECT COUNT(*) FROM article_clusters").fetchone()[0] == 1
        assert con_two.execute("SELECT COUNT(*) FROM article_clusters").fetchone()[0] == 1
    finally:
        con_one.close()
        con_two.close()
