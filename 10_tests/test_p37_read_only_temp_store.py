"""P37:`read_only_connect` 必须把临时 B-tree 放内存,否则大库聚合读当场失败。

真实缺陷(2026-11-17 T-14 旧资产迁移后实测):social 库涨到 40,506 行之后,
`ttxt doctor` 的 `db.url_identity` 报
`URL 身份对账读失败:OperationalError: unable to open database file`。

根因:`store.read_only_connect` 只设了 `PRAGMA query_only=ON`,没设 `temp_store`。
`GROUP BY source, url_normalized` 的临时 B-tree 一旦从内存升级到磁盘临时库就要在
TMP 落文件,而本项目环境落不了(P12 批次4 同一个坑,当时只在 `ttxt/db.py:102`
的**写连接**上修了,只读连接漏了 —— 库小的时候永远不升级,所以一直没暴露)。
"""

from __future__ import annotations

import pytest

from ttxt.crawler import store
from ttxt.crawler.extract import ExtractedItem

# 300 行 8000 字正文足够把 GROUP BY 的临时 B-tree 顶到磁盘(实测)
ROWS = 300
BODY = ("正文内容样本" * 2000)[:8000]


def _seed(db_path) -> None:
    store.migrate_articles_db("demo", db_path=db_path)
    items = [
        ExtractedItem(
            url=f"https://example.com/a{i}",
            title=f"标题{i}",
            content=BODY,
            excerpt="",
            author=None,
            published_at=None,
            source_item_id=f"p37-{i}",
        )
        for i in range(ROWS)
    ]
    assert store.insert_items("demo", "unit", items, db_path=db_path)["inserted"] == ROWS


def test_read_only_connect_keeps_temp_store_in_memory(tmp_path):
    db_path = tmp_path / "articles.db"
    _seed(db_path)
    con = store.read_only_connect(db_path)
    try:
        # PRAGMA temp_store 回读:1=FILE, 2=MEMORY
        assert con.execute("PRAGMA temp_store").fetchone()[0] == 2
    finally:
        con.close()


def test_doctor_style_group_by_survives_read_only(tmp_path):
    """`doctor._url_identity_row` 用的就是这条 GROUP BY —— 只读连接下必须能跑完。

    钉住修复的是上一条(临时库必须在内存):临时 B-tree 要不要落盘由数据量与当时
    TMP 能不能写共同决定,所以"退回 FILE 就会炸"在本机可写 TMP 下不稳定,不进断言。
    """
    db_path = tmp_path / "articles.db"
    _seed(db_path)
    con = store.read_only_connect(db_path)
    try:
        groups = list(
            con.execute(
                "SELECT source, url_normalized, COUNT(*) FROM articles"
                " GROUP BY source, url_normalized"
            )
        )
    finally:
        con.close()
    assert len(groups) == ROWS
    assert {row[0] for row in groups} == {"unit"}


def test_read_only_connection_still_cannot_write(tmp_path):
    """补 temp_store 不得把只读约束一起放宽(跨库读的红线)。"""
    db_path = tmp_path / "articles.db"
    _seed(db_path)
    con = store.read_only_connect(db_path)
    try:
        with pytest.raises(Exception):
            con.execute("UPDATE articles SET title='改了'")
        assert con.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == ROWS
    finally:
        con.close()
