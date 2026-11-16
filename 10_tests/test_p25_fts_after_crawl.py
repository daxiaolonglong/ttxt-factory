"""P25 根因测试:采集路径不建 FTS 索引 ⇒ 当天采回来的文章搜不到。

事实来源(生产实测,2026-11-16):`ttxt doctor` 新增 `db.fts_coverage` 第一次跑就抓到
374 行里只有 354 行进索引(social 22/33、technology 159/168),而 `articles_fts` 只在
`ttxt analyze → rag.rebuild_index` 里写(`ttxt/analysis/rag.py:22-42`);采集任务
(`ttxt/crawler/runner.py`)只跑清洗管线 `pipeline.run_pipeline`(`ttxt/crawler/pipeline.py`)。
⇒ 验收口径:管线跑完就必须进索引,不能等 analyze。
"""

from __future__ import annotations

import sqlite3

from ttxt.analysis import rag
from ttxt.crawler import pipeline, store
from ttxt.crawler.extract import ExtractedItem


def _item(title: str, content: str, url: str) -> ExtractedItem:
    return ExtractedItem(title=title, content=content, excerpt="", author="", published_at=None, url=url)


def _counts(db_path) -> tuple[int, int]:
    con = sqlite3.connect(db_path)
    try:
        return (
            con.execute("SELECT COUNT(*) FROM articles").fetchone()[0],
            con.execute("SELECT COUNT(*) FROM articles_fts").fetchone()[0],
        )
    finally:
        con.close()


def test_pipeline_puts_every_row_into_fts(tmp_path):
    """管线跑完:articles_fts 必须与 articles 一行不差(改前:0 行进索引)。"""
    db_path = store.init_articles_db("demo", db_path=tmp_path / "demo.db")
    items = [
        _item("国产量子计算原型机问世", "科研团队宣布国产量子计算原型机完成调试,量子比特数量再创新高。", "https://example.com/q1"),
        _item("开源大模型本地部署指南", "开发者可以用普通笔记本运行开源大模型,本地部署不再需要显卡。", "https://example.com/q2"),
    ]
    store.insert_items("demo", "test", items, db_path=db_path)
    stats = pipeline.run_pipeline("demo", db_path=db_path)

    assert stats["indexed"] == 2
    assert _counts(db_path) == (2, 2)


def test_fresh_article_searchable_without_analyze(tmp_path):
    """不跑 analyze,刚采回来的文章也要能被 `ttxt search` 命中(改前:命中 0 条)。"""
    db_path = store.init_articles_db("demo", db_path=tmp_path / "demo.db")
    store.insert_items(
        "demo",
        "test",
        [_item("量子计算新进展", "研究团队发布新的量子计算实验结果,纠错能力显著提升。", "https://example.com/q3")],
        db_path=db_path,
    )
    pipeline.run_pipeline("demo", db_path=db_path)

    hits = rag.search("demo", "量子计算", db_path=db_path)
    assert [hit["title"] for hit in hits] == ["量子计算新进展"]


def test_fts_rebuild_is_idempotent_and_follows_content_upgrade(tmp_path):
    """连跑两次不重复建行;正文升级后索引跟着换(旧词搜不到、新词搜得到)。"""
    db_path = store.init_articles_db("demo", db_path=tmp_path / "demo.db")
    store.insert_items(
        "demo",
        "test",
        [_item("电池回收新路线", "旧电池可以被完整拆解回收,锂回收率超过九成,废料不再填埋。", "https://example.com/b1")],
        db_path=db_path,
    )
    pipeline.run_pipeline("demo", db_path=db_path)
    pipeline.run_pipeline("demo", db_path=db_path)
    assert _counts(db_path) == (1, 1)

    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "UPDATE articles SET content = ?, cleaned_content = NULL WHERE url_normalized = ?",
            ("超级电容车在港口短驳运输中批量上线,五分钟快充即可跑完整个工作班次。", "https://example.com/b1"),
        )
        con.commit()
    finally:
        con.close()
    pipeline.run_pipeline("demo", db_path=db_path)

    assert [hit["title"] for hit in rag.search("demo", "超级电容", db_path=db_path)] == ["电池回收新路线"]
    assert rag.search("demo", "锂回收", db_path=db_path) == []
