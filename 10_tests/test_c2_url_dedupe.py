"""C-2 同类目 URL 重复插入:验收测试(先写测试,后改实现)。

缺陷实测依据(2026-11-15 只读核查,副本分析见 ``07_tmp/c2_20261115/dup_pre.json``):

- technology/articles.db:``https://www.toutiao.com/article/7486434702922531379/``
  有 3 行(id 253/261/268),标题完全相同、content_hash 各不相同,分别抓于
  02:27/02:33/02:39;后两次在 ``crawl_jobs`` 里被记成 inserted(谎报)。
- finance/articles.db:同一条 eastmoney 链接 2 行(id 1/65),标题随页面更新变化。
- demo/articles.db:``quotes_toscrape`` 的 30 行共用 ``https://quotes.toscrape.com/``,
  因为 html 列表页抽取器把**来源页地址**赋给每一条(``extract.py:55``);
  这个 URL 不是文章身份,按 URL 去重会误删 29 条合法数据。

由此确定本文件的判定原则(也是实现的判定原则):

1. 条目自带文章级链接(RSS/Atom 的 link、头条适配器规范化后的文章 URL)时,
   同一个类目库内同一规范化 URL 只允许一行;再采到就是 duplicate。
2. 条目 URL 等于来源页地址(抽取器没有逐条链接)时,URL 不构成文章身份,
   仍只按 content_hash 去重 —— 不因此丢数据。
3. “先查 URL 再插入”必须整体处于写事务保护内,并发任务不会双双插入。

不覆盖(明确属于其它任务):C-3 跨类目归属、C-1 正文完整性判定。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from types import SimpleNamespace

import pytest

from ttxt import config as cfg
from ttxt import db
from ttxt.crawler import clean, extract, fetcher, robots, runner, sources, store

CATEGORY = "c2"
ARTICLE_URL = "https://www.toutiao.com/article/7486434702922531379/"


# --------------------------------------------------------------------- 通用辅助


def _item(title: str, content: str, url: str, source_item_id: str | None = None) -> extract.ExtractedItem:
    return extract.ExtractedItem(
        title=title,
        content=content,
        excerpt="",
        author=None,
        published_at=None,
        url=url,
        source_item_id=source_item_id,
    )


def _rows(db_path) -> list[dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in con.execute(
                "SELECT id, category, source, title, url, url_normalized, content_hash, content"
                " FROM articles ORDER BY id"
            )
        ]
    finally:
        con.close()


def _dup_groups(db_path) -> list[tuple[str, int]]:
    """类目库内 url_normalized 重复组(URL, 行数),只保留 >1 的组。"""
    con = sqlite3.connect(db_path)
    try:
        return [
            (row[0], int(row[1]))
            for row in con.execute(
                "SELECT url_normalized, COUNT(*) FROM articles GROUP BY url_normalized HAVING COUNT(*) > 1"
            )
        ]
    finally:
        con.close()


# --------------------------------------------------------- 1 文章级 URL:同类目只留一行


def test_same_url_different_content_inserts_only_once(tmp_path):
    """重采同一篇文章(正文被站点重渲染):不得出现第二条,任务不得记 inserted。"""
    path = store.init_articles_db(CATEGORY, db_path=tmp_path / "c2.db")
    first = store.insert_items(
        CATEGORY, "toutiao_tech", [_item("抖音2025年3月侵权举报受理公示", "第一次抓到的正文内容甲。", ARTICLE_URL)],
        db_path=path,
    )
    assert first == {"inserted": 1, "duplicates": 0}
    second = store.insert_items(
        CATEGORY, "toutiao_tech", [_item("抖音2025年3月侵权举报受理公示", "第二次重渲染的正文内容乙。", ARTICLE_URL)],
        db_path=path,
    )
    # 修复前:second == {"inserted": 1, "duplicates": 0},库里 2 行
    # C-7b(2026-11-15)起同一 URL 位上出现正文不同的文章必须**显式计数**,不得只混进
    # duplicates(实测:老实现把这种塌陷当"正常重复"静默了 3 天,见 P17 §五 待办 2)
    # P20(2026-11-15,实测 job 64→66)细化了同一判据的另一半:**同一 URL + 同一标题**
    # 且正文更长 ⇒ 这是同一篇文章拿到更完整正文(第一轮 RSS 摘要 98 字、第二轮文章页
    # 900+ 字),按 C-7 的保留判据(正文最长者胜)就地升级这一行,不新增行、不改 id;
    # 只有标题不同才算"另一个身份"。本用例真正的口径没变:库里仍然只有一行。
    assert second == {"inserted": 0, "duplicates": 1, "bodies_upgraded": 1}
    rows = _rows(path)
    assert len(rows) == 1
    assert rows[0]["content"] == "第二次重渲染的正文内容乙。", "正文只允许向更完整的方向变,不得越改越短"
    assert _dup_groups(path) == []


def test_third_crawl_still_refused(tmp_path):
    """第三次重采同样被拒绝(不是只挡一次)。"""
    path = store.init_articles_db(CATEGORY, db_path=tmp_path / "c2.db")
    for index in range(3):
        counts = store.insert_items(
            CATEGORY, "toutiao_tech",
            [_item("同一篇文章", f"第{index}次抓到的正文内容。", ARTICLE_URL)],
            db_path=path,
        )
        assert counts == (
            {"inserted": 1, "duplicates": 0}
            if index == 0
            else {"inserted": 0, "duplicates": 1, "url_identity_conflict": 1}
        )
    assert len(_rows(path)) == 1


def test_same_batch_same_url_different_content_keeps_one(tmp_path):
    """同一批次里同一文章 URL 出现两次(列表页两条链接指向同一文章):只留一条。"""
    path = store.init_articles_db(CATEGORY, db_path=tmp_path / "c2.db")
    counts = store.insert_items(
        CATEGORY, "src",
        [
            _item("标题甲", "正文内容甲一二三。", "https://example.com/c2/batch"),
            _item("标题甲", "正文内容乙一二三。", "https://example.com/c2/batch"),
        ],
        db_path=path,
    )
    # 修复前:{"inserted": 2, "duplicates": 0}
    # C-7b:同批同 URL 正文不同 → 除 duplicates 还必须显式报 url_identity_conflict
    assert counts == {"inserted": 1, "duplicates": 1, "url_identity_conflict": 1}
    assert len(_rows(path)) == 1


def test_url_identity_is_scoped_to_the_category_database(tmp_path):
    """类目库即类目命名空间:同一个库内同一 URL 不得因 category 取值不同而绕过。"""
    path = store.init_articles_db(CATEGORY, db_path=tmp_path / "c2.db")
    assert store.insert_items(CATEGORY, "src", [_item("标题甲", "正文内容甲。", "https://example.com/c2/scoped")], db_path=path) == {
        "inserted": 1,
        "duplicates": 0,
    }
    assert store.insert_items("c2other", "src", [_item("标题甲", "正文内容乙。", "https://example.com/c2/scoped")], db_path=path) == {
        "inserted": 0,
        "duplicates": 1,
        "url_identity_conflict": 1,
    }
    assert len(_rows(path)) == 1


def test_same_url_in_different_category_databases_kept(tmp_path):
    """跨类目隔离不变(C-3 未动):不同类目库各自保留各自的一行。"""
    tech = store.init_articles_db("technology", db_path=tmp_path / "technology.db")
    fin = store.init_articles_db("finance", db_path=tmp_path / "finance.db")
    item = _item("同一条链接的文章", "同一正文内容一二三。", "https://finance.eastmoney.com/news/1345,202611123929972290.html")
    assert store.insert_items("technology", "eastmoney_rss", [item], db_path=tech) == {"inserted": 1, "duplicates": 0}
    assert store.insert_items("finance", "eastmoney_rss", [item], db_path=fin) == {"inserted": 1, "duplicates": 0}
    assert len(_rows(tech)) == 1 and len(_rows(fin)) == 1


def test_same_title_different_url_not_treated_as_duplicate(tmp_path):
    """不同 URL、相同标题不得按 URL 重复误判。"""
    path = store.init_articles_db(CATEGORY, db_path=tmp_path / "c2.db")
    counts = store.insert_items(
        CATEGORY, "src",
        [
            _item("完全相同的标题", "正文内容甲一二三。", "https://example.com/c2/title-1"),
            _item("完全相同的标题", "正文内容乙一二三。", "https://example.com/c2/title-2"),
        ],
        db_path=path,
    )
    assert counts == {"inserted": 2, "duplicates": 0}
    assert len(_rows(path)) == 2


# ----------------------------------------------------------- 2 URL 规范化的边界


def test_tracking_param_variants_count_as_same_url(tmp_path):
    """按既有规范化规则(只去跟踪参数)等价的两个 URL 判为同一篇文章。"""
    path = store.init_articles_db(CATEGORY, db_path=tmp_path / "c2.db")
    first = store.insert_items(
        CATEGORY, "src", [_item("分享链接文章", "正文内容甲一二三。", "https://example.com/c2/utm?utm_source=weibo")], db_path=path
    )
    second = store.insert_items(
        CATEGORY, "src", [_item("分享链接文章", "正文内容乙一二三。", "https://example.com/c2/utm?utm_medium=x#top")], db_path=path
    )
    assert first == {"inserted": 1, "duplicates": 0}
    assert second == {"inserted": 0, "duplicates": 1, "url_identity_conflict": 1}
    assert [row["url_normalized"] for row in _rows(path)] == ["https://example.com/c2/utm"]


def test_identifying_query_params_not_merged(tmp_path):
    """可能区分文章的查询参数不得被剥离:page=1 与 page=2 仍是两篇。"""
    path = store.init_articles_db(CATEGORY, db_path=tmp_path / "c2.db")
    counts = store.insert_items(
        CATEGORY, "src",
        [
            _item("分页文章第一页", "第一页正文内容一二三。", "https://example.com/c2/page?page=1"),
            _item("分页文章第二页", "第二页正文内容一二三。", "https://example.com/c2/page?page=2"),
        ],
        db_path=path,
    )
    assert counts == {"inserted": 2, "duplicates": 0}
    assert {row["url_normalized"] for row in _rows(path)} == {
        "https://example.com/c2/page?page=1",
        "https://example.com/c2/page?page=2",
    }
    assert clean.normalize_url("HTTPS://Example.COM:443/c2/a/?b=2&a=1#frag") == "https://example.com/c2/a/?a=1&b=2"


# --------------------------------------------------------------- 3 既有关键行为不变


def test_content_hash_dedupe_still_applies(tmp_path):
    """content_hash 唯一索引的去重行为必须保持:正文完全相同 → duplicate(修复前后一致)。"""
    path = store.init_articles_db(CATEGORY, db_path=tmp_path / "c2.db")
    item = _item("同一正文", "一字不差的正文内容一二三。", "https://example.com/c2/original")
    assert store.insert_items(CATEGORY, "src", [item], db_path=path) == {"inserted": 1, "duplicates": 0}
    copy = _item("同一正文", "一字不差的正文内容一二三。", "https://example.com/c2/mirror")
    assert store.insert_items(CATEGORY, "src", [copy], db_path=path) == {"inserted": 0, "duplicates": 1}
    assert len(_rows(path)) == 1


def test_page_level_listing_url_items_not_collapsed(tmp_path):
    """列表页抽取器把来源页 URL 赋给每条:30 条不得因 URL 相同被合并(demo 库实测形态)。"""
    path = store.init_articles_db(CATEGORY, db_path=tmp_path / "c2.db")
    items = [_item(f"引文{i}", f"引文正文内容{i}一二三。", "https://quotes.toscrape.com/") for i in range(30)]
    counts = store.insert_items(CATEGORY, "quotes_toscrape", items, db_path=path, page_url="https://quotes.toscrape.com/")
    # 修复前:{"inserted": 30, ...};修复后必须仍然是 30,否则就是新增的数据丢失
    # C-7b:同一 URL 上 30 篇不同文章是**塌陷**,必须当场计数(第一条占位不算,后 29 条各计 1);
    # 计数不改变落库结果:30 行全部保留(删了就毁掉真实语料)
    assert counts == {"inserted": 30, "duplicates": 0, "url_identity_conflict": 29}
    again = store.insert_items(CATEGORY, "quotes_toscrape", items, db_path=path, page_url="https://quotes.toscrape.com/")
    assert again == {"inserted": 0, "duplicates": 30}, "重采同一批是真重复,不得谎报成塌陷"
    assert len(_rows(path)) == 30


def test_mixed_batch_counts_match_database(tmp_path):
    """混合批次:计数必须与库内真实行数一致,不得谎报 inserted。"""
    path = store.init_articles_db(CATEGORY, db_path=tmp_path / "c2.db")
    seed = _item("已在库里的文章", "库里已有的正文内容甲。", "https://example.com/c2/seed")
    assert store.insert_items(CATEGORY, "src", [seed], db_path=path) == {"inserted": 1, "duplicates": 0}
    before = len(_rows(path))
    counts = store.insert_items(
        CATEGORY, "src",
        [
            _item("全新文章", "全新正文内容甲一二三。", "https://example.com/c2/fresh"),
            _item("同批重复链接", "同批正文内容甲一二三。", "https://example.com/c2/same-link"),
            _item("同批重复链接", "同批正文内容乙一二三。", "https://example.com/c2/same-link"),
            _item("已在库里的文章", "库里已有的正文内容甲。", "https://example.com/c2/seed"),
            _item("已在库里的文章", "库里已有的正文内容甲。", "https://example.com/c2/copy-hash"),
        ],
        db_path=path,
    )
    # fresh 新插入;same-link 第二条按 URL 重复;seed 已存在;copy-hash 与 seed 标题+正文完全一致
    # (content_hash 覆盖 title+content)→ 按 content_hash 重复
    assert counts == {"inserted": 2, "duplicates": 3, "url_identity_conflict": 1}
    assert len(_rows(path)) - before == counts["inserted"]
    assert _dup_groups(path) == []


# ------------------------------------------------------------- 4 并发与事务边界


def test_write_lock_covers_check_then_insert(tmp_path):
    """写锁必须在“检查 URL”之前取得:否则另一个事务提交后本批会插入第二条。"""
    path = store.init_articles_db(CATEGORY, db_path=tmp_path / "c2.db")
    seed = _item("占位文章", "占位正文内容一二三。", "https://example.com/c2/blocker")
    assert store.insert_items(CATEGORY, "seeder", [seed], db_path=path)["inserted"] == 1

    blocker = db.connect(path)
    blocker.execute("BEGIN IMMEDIATE")
    # 未提交的写:持住写锁 0.5s,模拟另一个采集任务正在写同一个类目库
    blocker.execute("UPDATE articles SET fetched_at = fetched_at WHERE url_normalized = ?",
                    (clean.normalize_url(seed.url),))
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(
            store.insert_items, CATEGORY, "racer",
            [_item("并发到达的同一文章", "并发抓到的正文内容乙。", "https://example.com/c2/blocker")],
            path,
        )
        finished_early = True
        try:
            future.result(timeout=0.25)
        except FuturesTimeout:
            finished_early = False
        blocker.commit()  # 另一个事务此刻才提交:该 URL 从此可见
        counts = future.result(timeout=30)
    finally:
        blocker.close()
        pool.shutdown(wait=True)
    assert not finished_early, "insert_items 在别的写事务持锁期间就已完成检查 → 检查不在写锁之内"
    assert counts == {"inserted": 0, "duplicates": 1, "url_identity_conflict": 1}
    assert len(_rows(path)) == 1  # 只有种子行:blocker 的 UPDATE 持锁但不新增行,竞争者也没有插入
    assert _dup_groups(path) == []


def test_two_threads_same_url_only_one_row(tmp_path):
    """两个采集任务同时插入同一 URL:只能有一条入库,其余记 duplicate。"""
    path = store.init_articles_db(CATEGORY, db_path=tmp_path / "c2.db")
    barrier = threading.Barrier(4)
    results: list[dict] = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        item = _item("并发同名文章", f"第{index}个任务抓到的正文内容。", "https://example.com/c2/race")
        barrier.wait(timeout=10)
        counts = store.insert_items(CATEGORY, f"racer{index}", [item], path)
        with lock:
            results.append(counts)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(4)]
    started = time.monotonic()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert all(not thread.is_alive() for thread in threads), "并发插入卡死未返回"
    assert len(results) == 4
    assert sum(counts["inserted"] for counts in results) == 1
    assert sum(counts["duplicates"] for counts in results) == 3
    assert len(_rows(path)) == 1
    assert _dup_groups(path) == []
    assert time.monotonic() - started < 30.0


def test_batch_failure_rolls_back_entire_batch(tmp_path):
    """批内异常:整批回滚,不留半批数据,也不产生任何计数谎报。"""
    path = store.init_articles_db(CATEGORY, db_path=tmp_path / "c2.db")
    with pytest.raises(ValueError):
        store.insert_items(
            CATEGORY, "mixer",
            [
                _item("正常文章", "正常正文内容一二三。", "https://example.com/c2/ok"),
                _item("坏 URL 文章", "坏正文内容一二三。", "http://[::1]:bad/x"),
            ],
            db_path=path,
        )
    assert len(_rows(path)) == 0
    # 回滚后同一 URL 仍可正常写入(证明确实没有留下未提交痕迹)
    assert store.insert_items(CATEGORY, "mixer", [_item("正常文章", "正常正文内容一二三。", "https://example.com/c2/ok")], db_path=path) == {
        "inserted": 1,
        "duplicates": 0,
    }


# ----------------------------------------------------------- 5 runner 层观测诚实性


class FakeRules:
    def __init__(self, disallow: tuple[str, ...] = (), unknown: bool = False):
        self.disallow = disallow
        self.unknown = unknown
        self.crawl_delay_s = None

    def allowed(self, path: str) -> bool:
        if self.unknown:
            return False
        return not any(path.startswith(p) for p in self.disallow)


class FakeCss:
    def __init__(self, items):
        self._items = list(items)

    def __len__(self):
        return len(self._items)

    def __getitem__(self, index):
        return self._items[index]

    def get(self):
        return self._items[0] if self._items else None

    def getall(self):
        return list(self._items)


class FakeElement:
    """Scrapling 元素的最小替身。实测(``07_tmp/p18_c7b_probe/probe_title.py``):真实元素
    有 ``tag``/``text``/``attrib`` 并且支持 ``css()``;C-7b 之后抽取器会向块元素要
    ``a::attr(href)``,替身必须实现,默认"块内没有链接"= 列表页没给逐条链接。"""

    def __init__(self, text="", attrib=None, tag: str = "div", links: dict[str, list[str]] | None = None):
        self.text = text
        self.attrib = attrib or {}
        self.tag = tag
        self._links = links or {}

    def css(self, selector: str):
        return FakeCss(
            [FakeElement(text=href, attrib={"href": href}, tag="a") for href in self._links.get(selector, [])]
        )


class FakePage:
    def __init__(self, mapping: dict[str, object]):
        self._mapping = mapping

    def css(self, selector: str):
        return FakeCss(self._mapping.get(selector, []))


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


@pytest.fixture
def categories_root(tmp_path, monkeypatch):
    """把类目数据根指向空目录:runner 的 ``insert_items`` 默认路径不会碰到生产库。"""
    categories = tmp_path / "01_categories"
    categories.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cfg, "CATEGORIES_DIR", categories)
    return categories


def _write_sources(tmp_path, monkeypatch, entries: list[dict]) -> None:
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"sources": entries}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(sources, "SOURCES_PATH", path)


def _rss_entry(**overrides) -> dict:
    entry = {
        "id": "c2_rss", "name": "c2 rss", "kind": "rss", "category": "demo",
        "url": "https://demo.example.com/feed.xml", "enabled": True, "render": "http",
        "rate_limit_s": 0.0, "max_pages": 1, "max_articles": 20, "demo": True,
    }
    entry.update(overrides)
    return entry


def _html_entry(**overrides) -> dict:
    entry = {
        "id": "c2_html", "name": "c2 html", "kind": "html", "category": "demo",
        "url": "https://demo.example.com/page1/", "enabled": True, "render": "http",
        "rate_limit_s": 0.0, "max_pages": 1, "item_selector": ".quote",
        "title_selector": None, "author_selector": None, "max_articles": 20, "demo": True,
    }
    entry.update(overrides)
    return entry


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


def test_repeat_rss_job_records_duplicates_not_inserted(tmp_path, monkeypatch, system_db, categories_root):
    """RSS 重采同一链接(站点改了标题/正文):库里仍一行,任务必须记 duplicate。"""
    _write_sources(tmp_path, monkeypatch, [_rss_entry()])
    monkeypatch.setattr(robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules())
    link = "https://demo.example.com/a/1/"
    monkeypatch.setattr(
        fetcher, "fetch_page",
        FetchRecorder({"https://demo.example.com/feed.xml": _feed(link, "美股三大指数涨跌不一 英伟达大跌", "英伟达大跌13%,市值蒸发超5000亿美元。")}),
    )
    store.create_crawl_job("c2_rss", "demo", db_path=system_db)
    first = runner.run_queued(limit=1)
    assert first and first[0]["ok"] is True
    assert first[0]["inserted"] == 1 and first[0]["duplicates"] == 0

    monkeypatch.setattr(
        fetcher, "fetch_page",
        FetchRecorder({"https://demo.example.com/feed.xml": _feed(link, "美股三大指数集体上涨 英伟达跌幅收窄", "费城半导体指数转涨,英伟达跌幅收窄。")}),
    )
    store.create_crawl_job("c2_rss", "demo", db_path=system_db)
    second = runner.run_queued(limit=1)
    assert second and second[0]["ok"] is True
    # 修复前:inserted=1 / duplicates=0 —— 观测谎报,库里出现第二条
    assert second[0]["inserted"] == 0 and second[0]["duplicates"] == 1

    articles_db = categories_root / "demo" / "articles.db"
    assert len(_rows(articles_db)) == 1
    assert _dup_groups(articles_db) == []
    jobs = _jobs(system_db)
    assert [json.loads(job["meta_json"])["inserted"] for job in jobs] == [1, 0]
    assert [json.loads(job["meta_json"])["duplicates"] for job in jobs] == [0, 1]


def test_html_listing_crawl_keeps_all_items_and_counts_second_crawl_duplicate(tmp_path, monkeypatch, system_db, categories_root):
    """列表页来源:每条正文不同也都在同一 URL 下 → 不得被 URL 规则合并;重复采集算 duplicate。

    这条同时钉住 ``runner`` 必须把来源页地址传给 ``insert_items``(page_url)。
    """
    _write_sources(tmp_path, monkeypatch, [_html_entry()])
    monkeypatch.setattr(robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules())
    pages = {
        "https://demo.example.com/page1/": FakePage({
            ".quote": [FakeElement(text="第一块引文内容甲"), FakeElement(text="第二块引文内容乙")]
        })
    }
    monkeypatch.setattr(fetcher, "fetch_page", FetchRecorder(pages))
    store.create_crawl_job("c2_html", "demo", db_path=system_db)
    first = runner.run_queued(limit=1)
    assert first and first[0]["ok"] is True
    assert first[0]["inserted"] == 2 and first[0]["duplicates"] == 0

    store.create_crawl_job("c2_html", "demo", db_path=system_db)
    second = runner.run_queued(limit=1)
    assert second and second[0]["ok"] is True
    assert second[0]["inserted"] == 0 and second[0]["duplicates"] == 2

    articles_db = categories_root / "demo" / "articles.db"
    rows = _rows(articles_db)
    assert len(rows) == 2
    assert rows[0]["url_normalized"] == rows[1]["url_normalized"] == "https://demo.example.com/page1/"
