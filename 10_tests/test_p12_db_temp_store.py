"""P12:db.connect 强制内存临时 B-tree 的回归测试。

真实缺陷:受限环境下 SQLite 无法在 TMP 创建磁盘临时库,导致
``DELETE FROM articles_fts``(索引重建)抛 OperationalError:
unable to open database file(P12 批次4 analyze technology 实测)。
修复:``PRAGMA temp_store=MEMORY``,兼作「一切文件仅 D:\\ttxt」边界防线。
"""

from __future__ import annotations

from ttxt import db
from ttxt.analysis import rag
from ttxt.crawler import store


def test_connect_sets_memory_temp_store(tmp_path):
    con = db.connect(tmp_path / "x.db")
    try:
        # PRAGMA temp_store 回读:1=FILE, 2=MEMORY
        assert con.execute("PRAGMA temp_store").fetchone()[0] == 2
    finally:
        con.close()


def test_rag_rebuild_twice_survives_fts_delete(tmp_path):
    from ttxt.crawler.extract import ExtractedItem

    db_path = tmp_path / "articles.db"
    # 与生产入口(analyze)一致:走 migrate,基线 DDL 缺 cleaned_content 由迁移补齐
    store.migrate_articles_db("demo", db_path=db_path)
    items = [
        ExtractedItem(
            url=f"https://example.com/a{i}",
            title=f"标题{i}",
            content="人工智能 数据 安全 治理 " * 10,
            excerpt="",
            author=None,
            published_at=None,
            source_item_id=f"unit-{i}",
        )
        for i in range(5)
    ]
    assert store.insert_items("demo", "unit", items, db_path=db_path)["inserted"] == 5
    assert rag.rebuild_index("demo", db_path=db_path)["indexed"] == 5
    # 第二次 rebuild 会对既有行执行 DELETE —— 正是旧环境下崩溃的路径
    assert rag.rebuild_index("demo", db_path=db_path)["indexed"] == 5
    # 第二次 rebuild 会对既有行执行 DELETE —— 正是旧环境下崩溃的路径
    assert rag.rebuild_index("demo", db_path=db_path)["indexed"] == 5
