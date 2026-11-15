"""C-3 跨类目库重复与命名空间归属:验收测试(先写测试,后改实现)。

缺陷实测依据(2026-11-15 只读核查,副本分析见 ``07_tmp/c3_20261115/cross_pre.json``):

- 磁盘上存在 6 个命名空间库:``demo finance health history social technology``;
  注册表(``00_control/category_registry.json``)只注册 5 个正式类目,``demo`` 未注册。
- 逐对 url/hash 交集只有一组:``demo`` ↔ ``technology`` = **13 条 URL、13 条 content_hash**
  (少数派 sspai.com/post/* 10 条 + ruanyifeng.com/blog 3 条),
  即同一批文章在两个库里各存一行;根因是这两个源在 P12 由 ``demo=true`` 转正为
  ``demo=false``(``02_crawler/sources.json`` 注释),旧的 demo 库无人认领也无人对账。
- 正式类目之间交集为 0;``history``/``social`` 有空库。也就是说:目前污染只在
  测试命名空间,但**写入侧没有任何跨库检测,体检也没有任何跨库对账**,
  再转正一个源、或同一文章被两个正式类目采到,就会静默复制且无从发现。

由此确定本文件的判定原则(也是实现的判定原则):

1. 正式类目之间互斥:同一规范化 URL 或同一 content_hash 已被某个**注册类目**持有,
   另一个注册类目不得再存一行;被拒的条目计入 ``duplicates``,不静默丢弃。
2. 未注册命名空间(如 ``demo``)没有数据所有权:它**不得**拦下正式类目的采集,
   否则测试数据会饿死正式语料;反过来,注册类目持有的文章不得再写进 ``demo``。
3. 跨库检查是**只读存在性查询**(实现用 ``PRAGMA query_only=ON``;``mode=ro`` URI 在
   WAL 库缺少 ``-wal/-shm`` 边车时会打开失败,见 ``doctor._namespace_identity``/
   ``store.read_only_connect`` 的注释),不得创建库、不得改其它库,
   也不得把不同类目库 JOIN 成混合检索上下文(规则见 ``08_wiki/db_schema.md`` §3)。
4. 归属要可观测:命中的命名空间与命中理由必须进任务 meta 与体检报告,
   否则运维只看到一个 "duplicates" 数字,不知道为什么少了一条。

不覆盖(明确属于其它任务):C-1 正文完整性判定;历史遗留重复行的清理(需单独授权)。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from ttxt import config as cfg
from ttxt import db, doctor
from ttxt.crawler import fetcher, robots, runner, sources, store

SSPAI_URL = "https://sspai.com/post/118935"
RUYI_URL = "http://www.ruanyifeng.com/blog/2026/11/weekly-issue-418.html"


# --------------------------------------------------------------------- 通用辅助


def _item(title: str, content: str, url: str) -> object:
    from ttxt.crawler import extract

    return extract.ExtractedItem(
        title=title,
        content=content,
        excerpt="",
        author=None,
        published_at=None,
        url=url,
        source_item_id=None,
    )


def _rows(db_path) -> list[dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in con.execute(
                "SELECT id, category, source, title, url, url_normalized, content_hash"
                " FROM articles ORDER BY id"
            )
        ]
    finally:
        con.close()


def _sha(path) -> tuple[int, str]:
    data = Path(path).read_bytes()
    return len(data), hashlib.sha256(data).hexdigest()


def _legacy_row(db_path, category: str, source_id: str, title: str, content: str, url: str) -> None:
    """直写一行,绕开写入侧判定:只用于构造“修复前已经落库”的历史重复行。

    修复后正式类目之间不可能再通过 ``insert_items`` 造出同一篇文章的两行,
    而体检对账必须能发现历史遗留 —— 所以测试用直写复现历史数据,而不是放宽实现。
    """
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO articles(category, source, source_item_id, title, content, excerpt,"
            " author, published_at, url, url_normalized, fetched_at, content_hash, completeness,"
            " credibility, processor_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                category,
                source_id,
                None,
                title,
                content,
                "",
                None,
                None,
                url,
                store.normalize_url(url),
                cfg.now_iso(),
                store.content_hash(title, content),
                "full",
                "unverified",
                store.PROCESSOR_VERSION,
            ),
        )
        con.commit()
    finally:
        con.close()


REGISTERED = ["technology", "finance", "health", "social", "history"]
DISPLAY = {
    "technology": "科技",
    "finance": "财经",
    "health": "健康",
    "social": "社会",
    "history": "历史",
}


@pytest.fixture
def namespaces(tmp_path, monkeypatch):
    """空的类目根 + 自建注册表:注册 5 个正式类目,``demo`` 故意不注册(与生产一致)。"""
    root = tmp_path / "01_categories"
    root.mkdir(parents=True, exist_ok=True)
    registry = tmp_path / "category_registry.json"
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "isolation": "physical",
                "categories": [
                    {"slug": slug, "display_name": DISPLAY[slug], "status": "active"}
                    for slug in REGISTERED
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "CATEGORIES_DIR", root)
    monkeypatch.setattr(cfg, "REGISTRY_PATH", registry)
    return root


# ------------------------------------------------- 1 正式类目之间:同一文章只允许属于一个库


def test_same_url_written_into_second_registered_category_is_refused(namespaces):
    """同一 URL 已在 technology 持有,finance 不得再存一行(实测:正式类目之间当前 0 交集,靠的就是没有第二次写入)。"""
    tech = store.init_articles_db("technology")
    fin = store.init_articles_db("finance")
    assert store.insert_items(
        "technology", "sspai_rss", [_item("程序员的工具链", "少数派原文正文甲。", SSPAI_URL)]
    ) == {"inserted": 1, "duplicates": 0}
    counts = store.insert_items(
        "finance", "some_finance_src", [_item("转载的工具链文章", "换了标题和正文的同一篇。", SSPAI_URL)]
    )
    # 修复前:{"inserted": 1, "duplicates": 0} —— 同一篇文章在两个类目库里各存一行
    assert counts == {"inserted": 0, "duplicates": 1}
    assert len(_rows(tech)) == 1
    assert _rows(fin) == []


def test_identical_content_different_url_cross_category_is_refused(namespaces):
    """正文与标题完全相同、URL 不同(一稿多投):content_hash 在正式类目之间同样是身份。"""
    store.init_articles_db("technology")
    fin = store.init_articles_db("finance")
    assert store.insert_items(
        "technology", "sspai_rss", [_item("同题同源", "完全一致的正文内容。", SSPAI_URL)]
    ) == {"inserted": 1, "duplicates": 0}
    counts = store.insert_items(
        "finance", "tmtpost_rss", [_item("同题同源", "完全一致的正文内容。", RUYI_URL)]
    )
    # 修复前:{"inserted": 1, "duplicates": 0}
    assert counts == {"inserted": 0, "duplicates": 1}
    assert _rows(fin) == []


def test_cross_report_names_holding_namespace(namespaces):
    """被拒条目必须给出归属:哪个命名空间持有、命中 URL 还是 hash。"""
    store.init_articles_db("technology")
    store.init_articles_db("finance")
    report: dict = {}
    counts = store.insert_items(
        "technology", "sspai_rss", [_item("甲", "正文甲。", SSPAI_URL)]
    )
    assert counts == {"inserted": 1, "duplicates": 0}
    counts = store.insert_items(
        "finance", "x", [_item("乙", "正文乙。", SSPAI_URL)], cross_report=report
    )
    assert counts == {"inserted": 0, "duplicates": 1}
    assert report["blocked"] == 1
    assert report["holders"]["technology"]["urls"] == [store.normalize_url(SSPAI_URL)]
    assert report["holders"]["technology"]["hashes"] == []


def test_third_registered_category_also_refused(namespaces):
    """互斥不是只挡第二个库:第三个库同样被拒。"""
    store.init_articles_db("technology")
    store.init_articles_db("finance")
    health = store.init_articles_db("health")
    store.insert_items("technology", "s", [_item("甲", "正文甲。", RUYI_URL)])
    for _ in range(2):
        counts = store.insert_items("finance", "s", [_item("乙", "正文乙改。", RUYI_URL)])
        assert counts == {"inserted": 0, "duplicates": 1}
    counts = store.insert_items("health", "s", [_item("丙", "正文丙改。", RUYI_URL)])
    assert counts == {"inserted": 0, "duplicates": 1}
    assert _rows(health) == []


# ------------------------------------- 2 未注册命名空间(demo):没有所有权,不得卡正式类目


def test_unregistered_namespace_never_gates_registered_category(namespaces):
    """demo 里已有这篇文章,technology 采集它必须成功(测试数据不得饿死正式语料)。

    这正是生产现状的复现:sspai/ruanyifeng 的 13 篇先在 demo 库,转正后必须能进 technology。
    """
    demo = store.init_articles_db("demo")
    store.insert_items("demo", "sspai_rss", [_item("少数派原文", "正文甲。", SSPAI_URL)])
    counts = store.insert_items(
        "technology", "sspai_rss", [_item("少数派原文", "正文甲。", SSPAI_URL)]
    )
    assert counts == {"inserted": 1, "duplicates": 0}
    assert len(_rows(demo)) == 1
    assert len(_rows(store.articles_db_path("technology"))) == 1


def test_registered_owner_blocks_write_into_unregistered_namespace(namespaces):
    """反过来:正式类目持有的文章不得再复制进 demo,以免正式语料被测试库复制污染。"""
    store.init_articles_db("technology")
    demo = store.init_articles_db("demo")
    store.insert_items("technology", "sspai_rss", [_item("少数派原文", "正文甲。", SSPAI_URL)])
    counts = store.insert_items("demo", "sspai_rss", [_item("少数派原文", "正文甲。", SSPAI_URL)])
    assert counts == {"inserted": 0, "duplicates": 1}
    assert _rows(demo) == []


def test_two_unregistered_namespaces_do_not_block_each_other(namespaces):
    """未注册命名空间之间没有所有权关系:不得互相拦截(否则测试链路会莫名少数据)。"""
    demo = store.init_articles_db("demo")
    other = store.init_articles_db("demo-legacy")
    assert store.insert_items("demo", "s", [_item("甲", "正文甲。", SSPAI_URL)]) == {
        "inserted": 1,
        "duplicates": 0,
    }
    assert store.insert_items("demo-legacy", "s", [_item("甲", "正文甲。", SSPAI_URL)]) == {
        "inserted": 1,
        "duplicates": 0,
    }
    assert len(_rows(demo)) == 1 and len(_rows(other)) == 1


# ------------------------------------------------------- 3 不得误伤:来源页地址不是文章身份


def test_page_level_items_are_not_cross_blocked_by_page_url(namespaces):
    """html 列表页把来源页地址赋给每一条(extract.py:55);两个类目采同一列表页不得被 URL 互斥合并。"""
    page = "https://demo.example.com/page1/"
    store.init_articles_db("demo")
    counts_tech = store.insert_items(
        "technology", "htmlsrc",
        [_item("引文甲", "第一块引文正文内容。", page), _item("引文乙", "第二块引文正文内容。", page)],
        page_url=page,
    )
    counts_demo = store.insert_items(
        "demo", "htmlsrc",
        [_item("引文甲", "第一块引文正文内容。", page), _item("引文乙", "第二块引文正文内容。", page)],
        page_url=page,
    )
    # 正文相同 → 被 content_hash 拦下(注册类目持有),但绝不是因为 URL 相同
    assert counts_tech == {"inserted": 2, "duplicates": 0}
    assert counts_demo == {"inserted": 0, "duplicates": 2}
    assert store.insert_items(
        "health", "htmlsrc",
        [_item("引文丙", "第三块引文正文内容。", page)],
        page_url=page,
    ) == {"inserted": 1, "duplicates": 0}


# ----------------------------------------------------- 4 只读纪律:不得创建/改动其它类目库


def test_cross_scan_is_read_only_and_creates_no_databases(namespaces):
    """跨库检查只能只读命中;不得为别的命名空间建库,也不得改动它的字节。"""
    tech = store.init_articles_db("technology")
    fin = store.init_articles_db("finance")
    before = _sha(fin)
    (namespaces / "history").mkdir(parents=True, exist_ok=True)  # 空目录,没有 articles.db
    assert store.insert_items(
        "technology", "s", [_item("甲", "正文甲。", SSPAI_URL)]
    ) == {"inserted": 1, "duplicates": 0}
    assert _sha(fin) == before
    assert not (namespaces / "history" / "articles.db").exists()
    assert (namespaces / "history").exists()
    counts = store.insert_items("finance", "s", [_item("乙", "正文乙。", SSPAI_URL)])
    assert counts == {"inserted": 0, "duplicates": 1}
    assert _sha(tech)[1]  # technology 被读,字节不因读而变
    assert len(_rows(tech)) == 1


# ------------------------------------------------------------- 5 体检:命名空间卫生 + 跨库交集


def test_doctor_reports_unregistered_namespace_and_cross_overlap(namespaces):
    """体检必须发现:未注册命名空间 + 正式类目之间的同一篇文章(修复前没有这项检查)。

    正式类目之间的交集已经不可能再用 ``insert_items`` 造出来 —— 那正是 C-3 写入侧
    拦住的动作。因此这里用直写(``_legacy_row``)模拟修复前就落在库里的重复行,
    体检必须把它抓出来并判定为 FAIL。
    """
    tech = store.init_articles_db("technology")
    fin = store.init_articles_db("finance")
    demo = store.init_articles_db("demo")
    # 历史顺序:先落在 demo(未注册),再转正进 technology —— 两步都合法,重复由此产生
    store.insert_items("demo", "sspai_rss", [_item("少数派原文", "正文甲。", SSPAI_URL)])
    store.insert_items("technology", "sspai_rss", [_item("少数派原文", "正文甲。", SSPAI_URL)])
    # 模拟修复前另一个正式类目也采到同一篇(修复后这条路已经被写入侧堵死)
    _legacy_row(fin, "finance", "s", "少数派原文", "正文甲。", SSPAI_URL)

    rows = doctor.namespace_overlap_rows()
    pair = next(row for row in rows if {row["left"], row["right"]} == {"finance", "technology"})
    assert pair["url_hits"] == 1
    assert pair["hash_hits"] == 1
    assert pair["registered_pair"] is True

    legacy_pair = next(row for row in rows if {row["left"], row["right"]} == {"demo", "technology"})
    assert legacy_pair["registered_pair"] is False

    checks = {name: (level, detail) for level, name, detail in doctor.namespace_checks()}
    level, detail = checks["namespaces.disk"]
    assert level == "WARN" and "demo" in detail
    assert checks["db.cross_overlap"][0] == "FAIL"


def test_doctor_passes_when_registered_categories_are_disjoint(namespaces):
    """正式类目互斥生效时体检必须 PASS,且未注册命名空间与正式库没有交集时不误报 FAIL。"""
    store.init_articles_db("technology")
    store.init_articles_db("finance")
    store.init_articles_db("demo")
    store.insert_items("technology", "s", [_item("甲", "正文甲。", SSPAI_URL)])
    counts = store.insert_items("demo", "s", [_item("甲改", "完全不同的另一篇正文。", RUYI_URL)])
    assert counts == {"inserted": 1, "duplicates": 0}
    checks = {name: (level, detail) for level, name, detail in doctor.namespace_checks()}
    assert checks["db.cross_overlap"][0] == "PASS"
    assert checks["namespaces.disk"][0] == "WARN"
    assert doctor.namespace_overlap_rows() == []


# --------------------------------------------------------------- 6 runner:归属进任务 meta


class FakeRules:
    """runner 会读 ``rules.unknown``(runner.py:131)与 ``crawl_delay_s``(:152),两者必须有。"""

    unknown = False
    crawl_delay_s = None

    def allowed(self, path: str) -> bool:
        return True


class FetchRecorder:
    def __init__(self, pages: dict[str, object]):
        self.pages = pages
        self.calls: list[str] = []

    def __call__(self, url, timeout=None, render=False, session=None):
        self.calls.append(url)
        return SimpleNamespace(page=self.pages[url], status=200, url=url, elapsed_s=0.0)


@pytest.fixture
def system_db(tmp_path, monkeypatch):
    path = tmp_path / "system.db"
    db.init_system_db(path)
    monkeypatch.setattr(store, "system_db_path", lambda: path)
    return path


def _write_sources(tmp_path, monkeypatch, entries: list[dict]) -> None:
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"sources": entries}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(sources, "SOURCES_PATH", path)


def _feed(link: str, title: str, body: str) -> SimpleNamespace:
    xml = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        f"<item><title>{title}</title><link>{link}</link><description>{body}</description>"
        "<pubDate>Tue, 11 Nov 2025 08:00:00 +0800</pubDate></item>"
        "</channel></rss>"
    )
    return SimpleNamespace(text=xml)


def _jobs(system_db) -> list[dict]:
    con = sqlite3.connect(system_db)
    con.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in con.execute("SELECT * FROM crawl_jobs ORDER BY id")]
    finally:
        con.close()


def test_runner_job_meta_records_cross_namespace_attribution(
    namespaces, system_db, tmp_path, monkeypatch
):
    """finance 采到 technology 已持有的文章:任务不得谎报 inserted,meta 里要写明归属。"""
    _write_sources(
        tmp_path, monkeypatch,
        [
            {
                "id": "c3_rss", "name": "c3 rss", "kind": "rss", "category": "finance",
                "url": "https://finance.example.com/feed.xml", "enabled": True, "render": "http",
                "rate_limit_s": 0.0, "max_pages": 1, "max_articles": 20, "demo": False,
            }
        ],
    )
    monkeypatch.setattr(robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules())
    monkeypatch.setattr(
        fetcher, "fetch_page",
        FetchRecorder({
            "https://finance.example.com/feed.xml": _feed(
                SSPAI_URL, "程序员的工具链", "少数派原文正文内容甲。"
            )
        }),
    )
    store.init_articles_db("technology")
    store.insert_items(
        "technology", "sspai_rss", [_item("程序员的工具链", "少数派原文正文内容甲。", SSPAI_URL)]
    )
    store.create_crawl_job("c3_rss", "finance", db_path=system_db)
    results = runner.run_queued(limit=1)
    assert results and results[0]["ok"] is True
    assert results[0]["inserted"] == 0 and results[0]["duplicates"] == 1

    meta = json.loads(_jobs(system_db)[0]["meta_json"])
    assert meta["cross_db"]["blocked"] == 1
    assert meta["cross_db"]["holders"]["technology"]["urls"] == [store.normalize_url(SSPAI_URL)]
    assert _rows(store.articles_db_path("finance")) == []


def test_runner_meta_omits_cross_db_key_when_there_is_no_overlap(
    namespaces, system_db, tmp_path, monkeypatch
):
    """没有跨库命中时 meta_json 结构不得变化(下游 dashboard/metrics 不需要特判)。"""
    _write_sources(
        tmp_path, monkeypatch,
        [
            {
                "id": "c3_rss", "name": "c3 rss", "kind": "rss", "category": "finance",
                "url": "https://finance.example.com/feed.xml", "enabled": True, "render": "http",
                "rate_limit_s": 0.0, "max_pages": 1, "max_articles": 20, "demo": False,
            }
        ],
    )
    monkeypatch.setattr(robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules())
    monkeypatch.setattr(
        fetcher, "fetch_page",
        FetchRecorder({
            "https://finance.example.com/feed.xml": _feed(
                "https://finance.example.com/a/1/", "A股三大指数收涨", "沪深两市成交额突破万亿。"
            )
        }),
    )
    store.create_crawl_job("c3_rss", "finance", db_path=system_db)
    results = runner.run_queued(limit=1)
    assert results and results[0]["inserted"] == 1
    meta = json.loads(_jobs(system_db)[0]["meta_json"])
    assert "cross_db" not in meta
    assert len(_rows(store.articles_db_path("finance"))) == 1
