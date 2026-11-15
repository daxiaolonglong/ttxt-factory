"""C-7b 抽取器 URL 身份塌陷:回归测试(实现与测试同批提交)。

实测依据(取证见 ``08_wiki/reports/P17-session-handoff-20261115.md`` §五 待办 2):

- ``ttxt/crawler/extract.py`` 老实现给列表页抽出的**每一条**赋 ``url=source.url``,
  于是 ``01_categories/demo/articles.db`` 里 30 条互不相同的名言共用同一个
  ``https://quotes.toscrape.com/`` —— URL 文章身份彻底塌陷;
- 入库侧当时只把这种情况按"正常重复" ``duplicates += 1`` 处理,既不计数也不记日志,
  所以塌陷静默了 3 天,直到 C-7 判重普查才看见。

由此确定本批的验收口径:

1. HTML 列表页抽取必须逐条取**自己**的链接;基准地址是**当前这一页**(分页后不是首页);
2. 页内两条抽到同一个链接 → 那个链接不是逐条链接(作者页/标签页就是这种),不采信,
   明确标记 ``url_is_page_fallback``,而不是静默塌陷;
3. RSS/Atom 缺 link 或相对 link 同样要明确标记 / 绝对化;
4. 入库侧"同一 URL 位上两篇不同文章"必须**计数 + 记日志**:
   文章级 URL → 仍然不插入(身份位只允许一行);非身份 URL(列表页)→ 正文保留(删了毁语料);
5. ``doctor.db.url_identity`` 按"责任来源今天是否仍启用"分级:仍启用 = FAIL,历史残留 = WARN。
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from ttxt import config as cfg
from ttxt import doctor
from ttxt.crawler import clean, dedupe, extract, sources, store

LIST_URL = "https://example.com/news/"
LIST_URL_P2 = "https://example.com/news/?page=2"
QUOTE_PAGE = "https://quotes.toscrape.com/"
FEED_URL = "https://sspai.com/feed"

# 标题里带逐条链接;作者页/标签页链接先出现在 DOM 里,且标签链接两条共用
LIST_HTML = """<html><body>
<article class="item">
  <a class="author" href="/author/douyin-official">抖音安全中心</a>
  <h2 class="title"><a href="/news/7486434702922531379.html">抖音2025年侵权举报受理公示</a></h2>
  <a class="tag" href="/tag/%E5%85%AC%E7%A4%BA">公示</a>
</article>
<article class="item">
  <a class="author" href="/author/other-author">另一个作者</a>
  <h2 class="title"><a href="/news/7399111111111111111.html">第二篇标题</a></h2>
  <a class="tag" href="/tag/%E5%85%AC%E7%A4%BA">公示</a>
</article>
</body></html>"""

# 卡片里只有作者页链接,而且两条同一个作者:没有任何"逐条链接"可用
NO_PERMALINK_HTML = """<html><body>
<div class="quote"><span class="text">第一条</span><a class="author" href="/author/Einstein">Einstein</a></div>
<div class="quote"><span class="text">第二条</span><a class="author" href="/author/Einstein">Einstein</a></div>
</body></html>"""

# 逐条链接要靠 link_selector 点名(标题不是链接)
LINK_SELECTOR_HTML = """<html><body>
<div class="quote"><span class="text">第一条</span><a class="tag" href="/tag/x">x</a><a class="more" href="/quote/1">详情</a></div>
<div class="quote"><span class="text">第二条</span><a class="tag" href="/tag/x">x</a><a class="more" href="/quote/2">详情</a></div>
</body></html>"""


def _source(**overrides) -> sources.Source:
    base = dict(
        id="fixture",
        name="fixture",
        category="technology",
        kind="html",
        url=LIST_URL,
        item_selector=".item",
        title_selector=".title",
        author_selector=".author",
        next_selector=None,
    )
    base.update(overrides)
    return sources.Source(**base)


def _page(html: str):
    from scrapling.parser import Selector

    return Selector(html)


# ----------------------------------------------------------- 抽取器:逐条取自己的链接


def test_each_item_gets_its_own_link():
    """标题里的链接就是这一条的文章地址,不是作者页/标签页。"""
    items = extract.extract_html_items(_page(LIST_HTML), _source(), page_url=LIST_URL)
    assert [item.url for item in items] == [
        "https://example.com/news/7486434702922531379.html",
        "https://example.com/news/7399111111111111111.html",
    ]
    assert not any(item.url_is_page_fallback for item in items)
    # 老实现赋的就是 source.url:必须证明现在不是
    assert all(item.url != LIST_URL for item in items)


def test_link_selector_wins_over_guessed_links():
    source = _source(item_selector=".quote", title_selector=".text", link_selector="a.more", author_selector=None)
    items = extract.extract_html_items(_page(LINK_SELECTOR_HTML), source, page_url=LIST_URL)
    assert [item.url for item in items] == ["https://example.com/quote/1", "https://example.com/quote/2"]
    assert not any(item.url_is_page_fallback for item in items)


def test_single_unlabeled_link_is_not_guessed():
    """卡片里只有一个链接但没有任何证据说它是文章地址 → 不猜,明确回退。

    实测取证(``07_tmp/p18_c7b_probe/probe_live_demo.py``,2026-11-15 抓真实列表页):
    这种"唯一的链接"恰恰是标签页 ``/tag/change/page/1/``,采信它等于给文章编一个假地址,
    比塌陷更隐蔽。整改出路是配 ``link_selector``,不是让抽取器猜。
    """
    html = """<html><body>
    <div class="quote"><span class="text">第一条引文</span><span class="tags">Tags: <a class="tag" href="/tag/aa/page/1/">aa</a></span></div>
    <div class="quote"><span class="text">第二条引文</span><span class="tags">Tags: <a class="tag" href="/tag/bb/page/1/">bb</a></span></div>
    </body></html>"""
    source = _source(item_selector=".quote", title_selector=".text", author_selector=None)
    items = extract.extract_html_items(_page(html), source, page_url=LIST_URL)
    assert all(item.url_is_page_fallback for item in items), "拿不准就回退标记,不得编造文章地址"
    assert {item.url for item in items} == {clean.normalize_url(LIST_URL)}


def test_multiple_unrelated_links_are_not_guessed():
    """卡片里多个链接却都不像标题:不许猜第一个——那往往是作者页/标签页,等于编地址。"""
    html = """<html><body>
    <div class="quote"><span class="text">第一条引文</span><a class="author" href="/author/A">A</a><a class="tag" href="/tag/aa">aa</a></div>
    <div class="quote"><span class="text">第二条引文</span><a class="author" href="/author/B">B</a><a class="tag" href="/tag/bb">bb</a></div>
    </body></html>"""
    source = _source(item_selector=".quote", title_selector=".text", author_selector=".author")
    items = extract.extract_html_items(_page(html), source, page_url=LIST_URL)
    assert all(item.url_is_page_fallback for item in items), "拿不准就回退标记,不得编造文章地址"
    assert {item.url for item in items} == {clean.normalize_url(LIST_URL)}


def test_shared_link_is_not_accepted_as_item_link():
    """两条共用同一个链接 → 那不是逐条链接;宁可标记回退,也不制造塌陷的文章 URL。"""
    source = _source(item_selector=".quote", title_selector=".text", author_selector=".author")
    items = extract.extract_html_items(_page(NO_PERMALINK_HTML), source, page_url=LIST_URL)
    assert len(items) == 2
    assert all(item.url_is_page_fallback for item in items), "拿不到逐条链接必须显式标记,不得静默"
    assert {item.url for item in items} == {clean.normalize_url(LIST_URL)}


def test_anchor_text_equal_to_title_is_adopted():
    """锚文本与标题一致(列表页常见:标题是纯文本,旁边一个同名链接)→ 采信。"""
    html = """<html><body>
    <div class="quote"><h2 class="tt">爱因斯坦论选择:世界是我们创造的产物</h2><a href="/news/748.html">爱因斯坦论选择:世界是我们创造的产物</a></div>
    <div class="quote"><h2 class="tt">两种生活方式:假装一切都存在或相信一切都存在</h2><a href="/news/749.html">两种生活方式:假装一切都存在或相信一切都存在</a></div>
    </body></html>"""
    source = _source(item_selector=".quote", title_selector=".tt", author_selector=None)
    items = extract.extract_html_items(_page(html), source, page_url=LIST_URL)
    assert [item.url for item in items] == ["https://example.com/news/748.html", "https://example.com/news/749.html"]
    assert not any(item.url_is_page_fallback for item in items)


def test_short_anchor_text_inside_sloppy_title_is_not_adopted():
    """标题选择器落空时 title 是整块文本;短锚文本(标签名)恰好命中也不得当成标题匹配。

    实测(07_tmp/p18_c7b_probe/probe_live_demo.py):宽松"互相包含"把 /tag/change/page/1/
    当成了文章地址,这就是比塌陷更隐蔽的假身份,必须挡住。
    """
    html = """<html><body>
    <div class="quote"><span itemprop="text">改变世界的选择,哈利,我们的选择表明了我们是谁。</span><span class="tags">Tags: <a class="tag" href="/tag/change/page/1/">change</a></span></div>
    <div class="quote"><span itemprop="text">只有两种生活方式:假装什么都不存在,或者认为一切都存在。</span><span class="tags">Tags: <a class="tag" href="/tag/life/page/1/">life</a></span></div>
    </body></html>"""
    source = _source(item_selector=".quote", title_selector=".text", author_selector=None)
    items = extract.extract_html_items(_page(html), source, page_url=LIST_URL)
    assert all(item.url_is_page_fallback for item in items), "短锚文本命中卡片文本不得当成标题匹配"
    assert {item.url for item in items} == {clean.normalize_url(LIST_URL)}


def test_relative_links_resolve_against_current_page():
    """分页到第 2 页时基准必须是第 2 页,否则相对链接指错页、也就取不到逐条链接。"""
    html = """<html><body><div class="quote"><span class="text">甲</span>
    <a class="more" href="detail/9.html">详情</a></div></body></html>"""
    source = _source(item_selector=".quote", title_selector=".text", author_selector=None, link_selector="a.more")
    items = extract.extract_html_items(_page(html), source, page_url=LIST_URL_P2)
    assert items[0].url == "https://example.com/news/detail/9.html"
    assert not items[0].url_is_page_fallback

    fallback = extract.extract_html_items(_page(NO_PERMALINK_HTML), _source(item_selector=".quote", title_selector=".text"), page_url=LIST_URL_P2)
    assert fallback[0].url == clean.normalize_url(LIST_URL_P2), "回退地址是当前页,不是首页"


def test_rss_missing_link_is_flagged_and_relative_link_absolutized():
    source = sources.Source(id="rss", name="rss", category="technology", kind="rss", url=FEED_URL)
    xml = """<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>没有链接的一条</title><description>正文</description></item>
    <item><title>相对链接的一条</title><link>/post/91234</link><description>正文</description></item>
    </channel></rss>"""
    items = extract.extract_rss_items(xml, source)
    assert items[0].url == FEED_URL and items[0].url_is_page_fallback, "缺 link 必须标记,不得静默"
    assert items[1].url == "https://sspai.com/post/91234"
    assert not items[1].url_is_page_fallback


# --------------------------------------------------------------- 入库侧:塌陷不得静默


def _item(title: str, content: str, url: str, *, fallback: bool = False) -> extract.ExtractedItem:
    return extract.ExtractedItem(
        title=title,
        content=content,
        excerpt=content[:40],
        author=None,
        published_at=None,
        url=url,
        url_is_page_fallback=fallback,
    )


def test_same_article_url_different_content_is_counted(tmp_path):
    """文章级 URL 上已经存着别的正文:仍然不插入,但必须计数,不得混进 duplicates 就完事。"""
    path = store.init_articles_db("_demo", db_path=tmp_path / "articles.db")
    first = store.insert_items("_demo", "src", [_item("甲", "正文甲" * 30, "https://example.com/a")], db_path=path)
    assert first == {"inserted": 1, "duplicates": 0}

    second = store.insert_items("_demo", "src", [_item("乙", "正文乙" * 30, "https://example.com/a")], db_path=path)
    assert second == {"inserted": 0, "duplicates": 1, "url_identity_conflict": 1}

    again = store.insert_items("_demo", "src", [_item("甲", "正文甲" * 30, "https://example.com/a")], db_path=path)
    assert again == {"inserted": 0, "duplicates": 1}, "同一篇被重复抓是真重复,不得谎报成身份塌陷"
    assert store.count_articles("_demo", db_path=path) == 1


def _article_row(db_path, url_normalized: str) -> sqlite3.Row:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT * FROM articles WHERE url_normalized = ?", (url_normalized,)
        ).fetchone()
    finally:
        con.close()


def test_same_url_same_title_longer_body_upgrades_the_row(tmp_path):
    """P20:同一 URL、同一标题、正文更长 ⇒ 就地升级这一行,不新增行,也不算塌陷。

    实测依据(job 64 → 66,2026-11-15 15:47 / 15:53):第一轮 RSS 只给 98 字摘要就入库,
    第二轮同一 URL 抓到文章页 900+ 字,老规则把它判成"身份塌陷"拒之门外 ——
    语料质量被永久锁死在摘要级,而且每一轮都会重复报 3 次假塌陷。
    """
    path = store.init_articles_db("_demo", db_path=tmp_path / "articles.db")
    short_body = "短摘要" * 10
    first = store.insert_items("_demo", "src", [_item("同一篇", short_body, "https://example.com/up")], db_path=path)
    assert first == {"inserted": 1, "duplicates": 0}
    before = _article_row(path, "https://example.com/up")
    assert before["completeness"] == "short"

    long_body = "中新网完整正文" * 80  # 560 字 ⇒ full(MIN_FULL_CHARS=400)
    second = store.insert_items("_demo", "src", [_item("同一篇 ", long_body, "https://example.com/up")], db_path=path)
    assert second == {"inserted": 0, "duplicates": 1, "bodies_upgraded": 1}, (
        "正文升级既不是新文章,也不是身份塌陷;标题差一个空格仍算同一篇"
    )

    after = _article_row(path, "https://example.com/up")
    assert after["id"] == before["id"], "升级必须落在原行上,不得新增第二行"
    assert after["fetched_at"] == before["fetched_at"], "首次抓取时间不得被改写"
    assert after["content"] == long_body and after["content_hash"] != before["content_hash"]
    assert after["completeness"] == "full"
    assert store.count_articles("_demo", db_path=path) == 1


def test_same_url_different_title_longer_body_is_still_conflict(tmp_path):
    """标题不同就是另一篇文章:哪怕正文更长也不得覆盖,仍按 C-7b 塌陷计数。"""
    path = store.init_articles_db("_demo", db_path=tmp_path / "articles.db")
    store.insert_items("_demo", "src", [_item("甲标题", "短摘要" * 10, "https://example.com/clash")], db_path=path)
    before = _article_row(path, "https://example.com/clash")

    counts = store.insert_items(
        "_demo", "src", [_item("乙标题", "中新网完整正文" * 80, "https://example.com/clash")], db_path=path
    )
    assert counts == {"inserted": 0, "duplicates": 1, "url_identity_conflict": 1}
    assert "bodies_upgraded" not in counts, "悄悄改标题正是 demo 30 条那类事故的做法"
    after = _article_row(path, "https://example.com/clash")
    assert after["content"] == before["content"] and after["title"] == "甲标题"


def test_page_url_collapse_counted_but_rows_kept(tmp_path):
    """列表页地址当文章地址:正文必须保留(删了毁语料),但塌陷必须当场计数。"""
    path = store.init_articles_db("_demo", db_path=tmp_path / "articles.db")
    batch = [
        _item("名言一", "内容一" * 30, LIST_URL, fallback=True),
        _item("名言二", "内容二" * 30, LIST_URL, fallback=True),
    ]
    counts = store.insert_items("_demo", "src", batch, db_path=path, page_url=LIST_URL, page_urls=[LIST_URL])
    assert counts == {"inserted": 2, "duplicates": 0, "url_identity_conflict": 1, "url_no_item_link": 2}
    assert store.count_articles("_demo", db_path=path) == 2, "塌陷行不得删除:删了就毁掉真实语料"

    rerun = store.insert_items(
        "_demo", "src", [_item("名言一", "内容一" * 30, LIST_URL, fallback=True)], db_path=path, page_url=LIST_URL
    )
    assert rerun == {"inserted": 0, "duplicates": 1, "url_no_item_link": 1}, "真重复不得再喊塌陷"


def test_batch_internal_conflict_counted(tmp_path):
    path = store.init_articles_db("_demo", db_path=tmp_path / "articles.db")
    counts = store.insert_items(
        "_demo",
        "src",
        [_item("甲", "正文甲" * 30, "https://example.com/same"), _item("乙", "正文乙" * 30, "https://example.com/same")],
        db_path=path,
    )
    assert counts == {"inserted": 1, "duplicates": 1, "url_identity_conflict": 1}


def test_no_conflict_keys_when_clean(tmp_path):
    """没有塌陷时返回结构必须保持两键字典(meta_json/dashboard 不需要为特例特判)。"""
    path = store.init_articles_db("_demo", db_path=tmp_path / "articles.db")
    counts = store.insert_items(
        "_demo", "src", [_item("甲", "正文甲" * 30, "https://example.com/one")], db_path=path
    )
    assert counts == {"inserted": 1, "duplicates": 0}


# ------------------------------------------------------------------------- 体检对账


def _db(root, slug: str):
    path = store.init_articles_db(slug)
    store.migrate_articles_db(slug, db_path=path)
    return path


def _insert_raw(db_path, source_id: str, title: str, content: str, url: str) -> int:
    """直写一行:复现"修复之前已经落库"的历史塌陷行。"""
    con = sqlite3.connect(str(db_path))
    try:
        cursor = con.execute(
            "INSERT INTO articles(category, source, source_item_id, title, content, excerpt,"
            " author, published_at, url, url_normalized, fetched_at, content_hash, completeness,"
            " credibility, processor_version) VALUES (?, ?, NULL, ?, ?, '', NULL, NULL, ?, ?, ?, ?,"
            " 'summary', 'unverified', ?)",
            (
                "technology",
                source_id,
                title,
                content,
                url,
                store.normalize_url(url),
                cfg.now_iso(),
                store.content_hash(title, content),
                store.PROCESSOR_VERSION,
            ),
        )
        con.commit()
        return int(cursor.lastrowid)
    finally:
        con.close()


@pytest.fixture
def url_identity_root(tmp_path, monkeypatch):
    """隔离类目根:``quotes_toscrape`` 已停用(历史残留),``sspai_rss`` 仍启用(今天在产)。"""
    root = tmp_path / "01_categories"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cfg, "CATEGORIES_DIR", root)
    monkeypatch.setattr(store.cfg, "CATEGORIES_DIR", root)
    monkeypatch.setattr(
        dedupe,
        "load_page_urls",
        lambda: [
            SimpleNamespace(url=QUOTE_PAGE, id="quotes_toscrape", enabled=False),
            SimpleNamespace(url=FEED_URL, id="sspai_rss", enabled=True),
        ],
    )
    return root


def _row(rows, name):
    return {(level, key): detail for level, key, detail in rows}


def test_url_identity_passes_when_urls_are_real_articles(url_identity_root):
    path = _db(url_identity_root, "technology")
    _insert_raw(path, "sspai_rss", "少数派的一篇文章", "正文" * 100, "https://sspai.com/post/118175")
    rows = {(level, name): detail for level, name, detail in doctor.completeness_checks()}
    assert rows[("PASS", "db.url_identity")] == "没有文章行把来源页/订阅地址写成自己的地址(URL 身份健康)"


def test_url_identity_warns_for_disabled_source_history(url_identity_root):
    path = _db(url_identity_root, "technology")
    for one in range(3):
        _insert_raw(path, "quotes_toscrape", f"名言{one}", f"内容{one}" * (one + 5), QUOTE_PAGE)
    rows = {(level, name): detail for level, name, detail in doctor.completeness_checks()}
    level, detail = rows[("WARN", "db.url_identity")][0], rows[("WARN", "db.url_identity")]
    assert "3 行" in detail and QUOTE_PAGE in detail
    assert "不得删除" in detail


def test_url_identity_fails_when_enabled_source_still_collapses(url_identity_root):
    """责任来源今天还启用 = 塌陷还在生产,必须 FAIL(不能像历史残留那样只 WARN)。"""
    path = _db(url_identity_root, "technology")
    _insert_raw(path, "sspai_rss", "少数派的一条", "正文一" * 30, FEED_URL)
    _insert_raw(path, "sspai_rss", "少数派的另一条", "正文二" * 40, FEED_URL)
    rows = {(level, name): detail for level, name, detail in doctor.completeness_checks()}
    detail = rows[("FAIL", "db.url_identity")]
    assert "仍启用" in detail and FEED_URL in detail
