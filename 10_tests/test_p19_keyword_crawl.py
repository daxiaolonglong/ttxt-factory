"""P19(2026-11-15)关键词驱动采集 + 正文补全:回归测试(实现与测试同批提交)。

根因(取证见 ``08_wiki/reports/P19-keyword-driven-crawl-20261115.md``):

1. ``analysis/keywords.py:auto_feedback`` 只会给类目**第一个启用来源**入一条普通采集任务
   (``:154``),关键词从不进入采集动作 ⇒ "补采"永远采同一个固定地址;
2. 中新网/澎湃这类源,RSS 与列表页只给标题级摘要(实测摘要中位数 71–108 字,
   见 ``07_tmp/p19_keyword_crawl/probe2.json``/``probe3.json``),
   直接入库就只能得到"标题 + 一句摘要",爆款研究没有正文可学;
3. 通用正文选择器在这两个站点**全部落空**(``probe5.json`` 17 个候选全 0),
   真实容器是 ``div.left_zw``(中新网)与哈希类名(澎湃,``probe7_tightest_container.json``)
   ⇒ 正文选择器必须由来源配置点名,猜不到就返回空串保留摘要,**绝不拿导航冒充正文**。

验收口径(本文件逐条钉住):

- 入队 meta 里的采集计划(关键词 / ``url_override``)必须被 runner 读出并生效;
  ``finish_crawl_job`` 会用回执覆盖 meta_json,所以回执里要回显 ``plan``;
- ``url_override`` 只允许**同 host**,跨 host 计划一律拒绝(否则类目路由与跨库判重全失效);
- 关键词闸门在入库侧生效,不命中的条目必须计数(``keyword_filtered_out``),不得静默丢弃;
- 正文补全逐条单独过 robots 门禁、有上限、失败保留原摘要并计数;
  全部失败且原本就没有像样正文 ⇒ 走可重试通道,不算完成;
- 计划阶段 robots 不放行的检索地址**当场拒绝**,不产生注定失败的任务。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from ttxt import config as cfg
from ttxt import db
from ttxt.crawler import cleaner, extract, keyword_crawl, robots, runner
from ttxt.crawler import sources as source_loader
from ttxt.crawler import store

FEED_URL = "https://demo.example.com/feed.xml"
SEARCH_URL = "https://demo.example.com/search.xml?q=%E6%B0%91%E7%94%9F"
OTHER_HOST = "https://other.example.com/feed.xml"
ARTICLE_URL = "https://demo.example.com/a/1/"

# R56 假语料纪律:RSS description/文章正文必须 ≥ cleaner.MIN_INTAKE_BODY_CHARS(800),
# 否则会被入库拒收线挡下 —— 只许把数据补厚,不许断言放宽,更不许迁就假数据调低阈值。
# 填充词刻意避开本文件全部测试关键词(民生/养老金/AI 医疗/医保/城市治理),
# 否则"无关"条目会被填充词自己命中。
BODY_FILLER = "这里是用于回归测试的完整正文示例段落。" * 45  # 855 字


# ---------------------------------------------------------------- 替身基础设施


class FakeRules:
    def __init__(self, disallow: tuple[str, ...] = (), unknown: bool = False):
        self.disallow = disallow
        self.unknown = unknown
        self.crawl_delay_s = None

    def allowed(self, path: str) -> bool:
        if self.unknown:
            return False
        return not any(path.startswith(p) for p in self.disallow)


class FakeElement:
    def __init__(self, text="", attrib=None, tag: str = "div"):
        self.text = text
        self.attrib = attrib or {}
        self.tag = tag

    def css(self, selector: str):
        return []


class FakePage:
    def __init__(self, mapping: dict[str, object]):
        self._mapping = mapping

    def css(self, selector: str):
        return list(self._mapping.get(selector, []))


class FetchRecorder:
    def __init__(self, pages: dict[str, object]):
        self.pages = pages
        self.calls: list[str] = []

    def __call__(self, url, timeout=None, render=False, session=None):
        self.calls.append(url)
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        return SimpleNamespace(page=page, status=200, url=url, elapsed_s=0.0)


@pytest.fixture
def system_db(tmp_path, monkeypatch):
    path = tmp_path / "system.db"
    db.init_system_db(path)
    monkeypatch.setattr(store, "system_db_path", lambda: path)
    return path


@pytest.fixture
def capture_insert(monkeypatch):
    """捕获 insert_items 的位置参数**和**关键字参数(page_url / page_urls 是 P19 断言点)。"""
    captured: list[dict] = []

    def _fake(category, source_id, items, *args, **kwargs):
        captured.append(
            {
                "category": category,
                "source_id": source_id,
                "items": list(items),
                "page_url": kwargs.get("page_url"),
                "page_urls": kwargs.get("page_urls"),
            }
        )
        return {"inserted": len(items), "duplicates": 0}

    monkeypatch.setattr(store, "insert_items", _fake)
    return captured


def write_sources(tmp_path: Path, monkeypatch, entries: list[dict]) -> Path:
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"sources": entries}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(source_loader, "SOURCES_PATH", path)
    return path


def rss_entry(**overrides) -> dict:
    entry = {
        "id": "fake_rss",
        "name": "fake rss",
        "kind": "rss",
        "category": "demo",
        "url": FEED_URL,
        "enabled": True,
        "render": "http",
        "rate_limit_s": 0.0,
        "max_pages": 1,
        "max_articles": 20,
        "demo": True,
    }
    entry.update(overrides)
    return entry


def formal_entry(**overrides) -> dict:
    """正式来源(demo=False):计划侧只认正式来源,演示来源不得进入关键词计划。"""
    overrides.setdefault("demo", False)
    overrides.setdefault("category", "social")
    return rss_entry(**overrides)


def feed_xml(
    titles: tuple[str, ...] = ("民生新闻甲", "无关内容乙"),
    descriptions: tuple[str, ...] | None = None,
) -> str:
    """RSS 假 feed。默认 description 用 ≥800 字填充词——过 R56 入库拒收线;
    只有故意测"摘要级正文"路径(all_failed)时才传 ``descriptions`` 短文本。"""
    items = "".join(
        f"<item><title>{title}</title><link>https://demo.example.com/a/{index}/</link>"
        f"<description>{descriptions[index - 1] if descriptions else BODY_FILLER + str(index)}</description>"
        f"<pubDate>Wed, 15 Nov 2026 10:00:0{index} +0800</pubDate></item>"
        for index, title in enumerate(titles, start=1)
    )
    return f"<?xml version='1.0'?><rss version='2.0'><channel>{items}</channel></rss>"


def selector_page(body_texts: list[str], selector: str = "div.left_zw") -> FakePage:
    return FakePage({selector: [FakeElement(text=text) for text in body_texts]})


def _real_page(html: str):
    from scrapling.parser import Selector

    return Selector(html)


# ------------------------------------------------------- 来源配置:新字段与正式来源注册


def test_new_source_fields_default_when_absent(tmp_path, monkeypatch):
    """老 sources.json 没有 P19 新字段必须照常加载(向后兼容,不要求改历史配置)。"""
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"sources": [rss_entry()]}), encoding="utf-8")
    loaded = source_loader.load_sources(path=path)
    assert loaded[0].keywords == [] and loaded[0].url_template is None
    assert loaded[0].fetch_item_pages is False and loaded[0].max_item_pages == 10
    assert loaded[0].item_content_selectors == []


def test_production_sources_cover_social_and_history():
    """social/history 长期 0 行是这次要解决的目标:正式来源白名单里必须有可用的合格源。

    只读生产 ``02_crawler/sources.json``,不碰任何数据库。
    """
    loaded = source_loader.load_sources(path=cfg.CRAWLER_DIR / "sources.json")
    for category in ("social", "history"):
        usable = [item for item in loaded if item.category == category and item.enabled and not item.demo]
        assert usable, f"{category} 没有启用中的正式来源"
        assert any(item.fetch_item_pages and item.item_content_selectors for item in usable), (
            f"{category} 的来源没有点名正文容器,补不到正文就只能存摘要级语料"
        )
        assert all(item.max_item_pages <= 40 for item in usable), "正文补全必须有时间上限"


# ---------------------------------------------------------------- 正文抽取器


def test_body_extractor_uses_source_selectors_first():
    source = source_loader.Source(
        id="s", name="s", category="social", kind="rss", url=FEED_URL,
        item_content_selectors=["div.left_zw", "div.content_maincontent_content"],
    )
    page = _real_page(
        "<html><body><div class='left_zw'>" + "中新网正文内容。" * 30 + "</div>"
        "<div class='nav'>导航栏内容不应被当成正文" * 3 + "</div></body></html>"
    )
    body = extract.extract_article_body(page, source)
    assert body.startswith("中新网正文内容")
    assert len(body) >= extract.MIN_BODY_CHARS
    assert "导航栏" not in body


def test_body_extractor_returns_empty_when_only_thin_text():
    """只有一句话摘要时不得冒充正文:返回空串由调用方保留原摘要。"""
    source = source_loader.Source(id="s", name="s", category="social", kind="rss", url=FEED_URL)
    page = _real_page("<html><body><article>只有一句摘要。</article></body></html>")
    assert extract.extract_article_body(page, source) == ""


def test_body_extractor_joins_multiple_matches():
    source = source_loader.Source(id="s", name="s", category="social", kind="rss", url=FEED_URL)
    page = _real_page(
        "<html><body><div class='article-content'><p>" + "第一段中文正文内容。" * 12
        + "</p></div><div class='article-content'><p>" + "第二段中文正文内容。" * 12 + "</p></div></body></html>"
    )
    body = extract.extract_article_body(page, source)
    assert "第一段" in body and "第二段" in body


def test_body_extractor_never_fabricates_when_selector_misses():
    source = source_loader.Source(id="s", name="s", category="social", kind="rss", url=FEED_URL,
                                  item_content_selectors=["div.not_on_this_site"])
    page = _real_page("<html><body><div class='main_content'>" + "整页文本很长" * 60 + "</div></body></html>")
    assert extract.extract_article_body(page, source) == ""


# ---------------------------------------------------------------- 关键词命中


def test_keyword_hit_matches_title_and_content_case_insensitive():
    item = extract.ExtractedItem(
        title="AI 医疗新政落地",
        content="正文提到 ai 医疗的落地细节",
        excerpt="正文提到 ai 医疗的落地细节",
        author=None,
        published_at=None,
        url="https://demo.example.com/a/1/",
    )
    hit = extract.keyword_hit(item, ["AI 医疗"])
    assert hit is not None
    assert hit == {
        "keyword": "AI 医疗",
        "in_title": True,
        "in_content": True,
        "in_excerpt": True,
    }
    # R56:命中位置必须能区分 —— 标题级命中与正文级命中在回执里可分开归因
    title_only = extract.ExtractedItem(
        title="养老金上调方案公布",
        content="与主题无关的正文段落",
        excerpt="与主题无关的正文段落",
        author=None,
        published_at=None,
        url="https://demo.example.com/a/2/",
    )
    assert extract.keyword_hit(title_only, ["养老金"]) == {
        "keyword": "养老金",
        "in_title": True,
        "in_content": False,
        "in_excerpt": False,
    }
    # 摘要位只留痕、不参与放行:仅摘要含词(标题/正文都不含)仍然不算命中
    excerpt_only = extract.ExtractedItem(
        title="无关标题",
        content="无关正文",
        excerpt="摘要里出现养老金",
        author=None,
        published_at=None,
        url="https://demo.example.com/a/3/",
    )
    assert extract.keyword_hit(excerpt_only, ["养老金"]) is None
    assert extract.keyword_hit(item, ["医保"]) is None
    assert extract.keyword_hit(item, []) is None


# ---------------------------------------------------------------- 任务参数读取


def test_crawl_job_params_roundtrip_and_missing(system_db):
    job = store.create_crawl_job("fake_rss", "demo", db_path=system_db, meta={"keyword_filter": ["民生"], "url_override": SEARCH_URL})
    params = store.crawl_job_params(job, db_path=system_db)
    assert params["keyword_filter"] == ["民生"] and params["url_override"] == SEARCH_URL
    assert store.crawl_job_params(999999, db_path=system_db) == {}
    con = db.connect(system_db)
    try:
        con.execute("UPDATE crawl_jobs SET meta_json='{坏 json' WHERE id=?", (job,))
        con.commit()
    finally:
        con.close()
    assert store.crawl_job_params(job, db_path=system_db) == {}


# ---------------------------------------------------------------- runner:计划生效


def test_keyword_plan_reaches_execution_and_filters(tmp_path, monkeypatch, system_db, capture_insert):
    """入队 meta 的关键词/检索地址必须真正生效:抓的是 override 地址,入库只剩命中条目。"""
    write_sources(tmp_path, monkeypatch, [rss_entry()])
    job = store.create_crawl_job(
        "fake_rss",
        "demo",
        db_path=system_db,
        meta={
            "url_override": SEARCH_URL,
            "keyword_filter": ["民生"],
            "keywords": ["民生"],
            "reason": "keyword_search",
        },
    )
    recorder = FetchRecorder({SEARCH_URL: SimpleNamespace(text=feed_xml())})
    monkeypatch.setattr("ttxt.crawler.fetcher.fetch_page", recorder)
    monkeypatch.setattr(robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules())

    results = runner.run_queued(limit=1)
    assert results and results[0]["ok"] is True
    assert recorder.calls == [SEARCH_URL], "override 生效后才该去抓检索地址"
    assert capture_insert[0]["page_url"] == SEARCH_URL
    assert [item.title for item in capture_insert[0]["items"]] == ["民生新闻甲"]
    assert results[0]["keyword_filtered_out"] == 1
    assert results[0]["keyword_hits"] == {"民生": {"count": 1, "in_title": 1, "in_content": 0}}
    assert results[0]["plan"]["url_override"] == SEARCH_URL
    meta = json.loads(_job_row(system_db, job)["meta_json"])
    assert meta["plan"]["keywords"] == ["民生"], "meta_json 被回执覆盖,计划必须回显在回执里"
    # R56 留痕:被关键词闸挡掉的条目必须逐条可查(URL/标题/拒因/判定依据),不许只计数
    assert meta["rejected"] == [
        {
            "url": "https://demo.example.com/a/2/",
            "title": "无关内容乙",
            "reason": "keyword_no_hit",
            "basis": {
                "keywords": ["民生"],
                "scope": "title+content",
                "body_len": len(BODY_FILLER) + 1,
            },
        }
    ]


def test_url_override_cross_host_is_rejected(tmp_path, monkeypatch, system_db, capture_insert):
    """跨 host 的 override 一律拒绝:类目路由、host 级 robots 缓存、跨库判重都靠来源 host。"""
    write_sources(tmp_path, monkeypatch, [rss_entry()])
    store.create_crawl_job("fake_rss", "demo", db_path=system_db, meta={"url_override": OTHER_HOST})
    recorder = FetchRecorder({FEED_URL: SimpleNamespace(text=feed_xml())})
    monkeypatch.setattr("ttxt.crawler.fetcher.fetch_page", recorder)
    monkeypatch.setattr(robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules())

    results = runner.run_queued(limit=1)
    assert results and results[0]["ok"] is True
    assert recorder.calls == [FEED_URL]
    assert results[0]["url_override_rejected"] == "cross_host"
    assert capture_insert[0]["page_url"] == FEED_URL


def _job_row(db_path: Path, job_id: int) -> sqlite3.Row:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return con.execute("SELECT * FROM crawl_jobs WHERE id=?", (job_id,)).fetchone()
    finally:
        con.close()


# ---------------------------------------------------------------- runner:正文补全


def test_item_pages_enrich_body_and_count(tmp_path, monkeypatch, system_db, capture_insert):
    write_sources(
        tmp_path,
        monkeypatch,
        [rss_entry(fetch_item_pages=True, max_item_pages=2, item_content_selectors=["div.left_zw"])],
    )
    article_html = "<html><body><div class='left_zw'>" + "中新网完整正文内容。" * 100 + "</div></body></html>"
    recorder = FetchRecorder(
        {FEED_URL: SimpleNamespace(text=feed_xml()), ARTICLE_URL: _real_page(article_html)}
    )
    monkeypatch.setattr("ttxt.crawler.fetcher.fetch_page", recorder)
    monkeypatch.setattr(robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules())
    store.create_crawl_job("fake_rss", "demo", db_path=system_db)

    results = runner.run_queued(limit=1)
    assert results and results[0]["ok"] is True
    assert results[0]["items_enriched"] == 1 and results[0]["item_pages_fetched"] == 1
    body = capture_insert[0]["items"][0].content
    assert body.startswith("中新网完整正文内容") and len(body) > 200


def test_item_page_cap_is_honest(tmp_path, monkeypatch, system_db, capture_insert):
    """3 条条目只补 1 页:必须记 capped,不得假装全部补全。"""
    write_sources(
        tmp_path,
        monkeypatch,
        [rss_entry(fetch_item_pages=True, max_item_pages=1, item_content_selectors=["div.left_zw"])],
    )
    article_html = "<html><body><div class='left_zw'>" + "正文内容一整段。" * 120 + "</div></body></html>"
    recorder = FetchRecorder(
        {FEED_URL: SimpleNamespace(text=feed_xml(("民生甲", "民生乙", "民生丙"))), ARTICLE_URL: _real_page(article_html)}
    )
    monkeypatch.setattr("ttxt.crawler.fetcher.fetch_page", recorder)
    monkeypatch.setattr(robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules())
    store.create_crawl_job("fake_rss", "demo", db_path=system_db)

    results = runner.run_queued(limit=1)
    assert results[0]["item_pages_capped"] == 1
    assert results[0]["items_enriched"] == 1
    assert len([call for call in recorder.calls if call.endswith("/a/1/")]) == 1


def test_keyword_gate_judges_after_body_enrichment(tmp_path, monkeypatch, system_db, capture_insert):
    """R56(负责人指令):关键词闸门改到正文补全**之后**判定(旧 P20 顺序作废)。

    旧 P20 把闸门排在补全前,代价是闸门只见到标题+列表摘要——R55 job 386 实证:
    宽词命中全部发生在标题/摘要层,标题党进了库、正文喂不了爆款判定。现在正文到手
    之后判:标题不含主题词、正文含的必须放行;"标题正文都无关"的仍然拒(判定集合
    不变,不放宽)。预算保护意图由 ``test_body_enrichment_budget_still_capped_after_gate_move``
    继承,不是删掉旧断言。
    """
    write_sources(
        tmp_path,
        monkeypatch,
        [rss_entry(fetch_item_pages=True, max_item_pages=10, item_content_selectors=["div.left_zw"])],
    )
    body_kw = "<html><body><div class='left_zw'>民生领域深度观察:" + "中新网完整正文内容。" * 100 + "</div></body></html>"
    body_plain = "<html><body><div class='left_zw'>" + "中新网完整正文内容。" * 100 + "</div></body></html>"
    pages: dict[str, object] = {
        # 甲:标题不含"民生"(旧顺序下会被误杀),正文含
        FEED_URL: SimpleNamespace(text=feed_xml(("新闻甲", "无关内容乙")))
    }
    pages["https://demo.example.com/a/1/"] = _real_page(body_kw)
    pages["https://demo.example.com/a/2/"] = _real_page(body_plain)
    recorder = FetchRecorder(pages)
    monkeypatch.setattr("ttxt.crawler.fetcher.fetch_page", recorder)
    monkeypatch.setattr(robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules())
    job = store.create_crawl_job(
        "fake_rss", "demo", db_path=system_db, meta={"keyword_filter": ["民生"], "keywords": ["民生"]}
    )

    results = runner.run_queued(limit=1)
    assert results and results[0]["ok"] is True
    assert results[0]["feed_total"] == 2, "feed_total 仍是订阅源真实条数"
    assert results[0]["keyword_filtered_out"] == 1
    assert [item.title for item in capture_insert[0]["items"]] == ["新闻甲"], (
        "正文命中的条目必须放行——这正是闸门后移要救回来的那一类"
    )
    assert results[0]["keyword_hits"] == {"民生": {"count": 1, "in_title": 0, "in_content": 1}}, (
        "命中位置要能区分:这一条是正文级命中,不是标题级"
    )
    # 诚实代价:补全发生在闸门前,"无关内容乙"的文章页也被抓了(有 max_item_pages 封顶兜底)
    assert recorder.calls == [FEED_URL, "https://demo.example.com/a/1/", "https://demo.example.com/a/2/"]
    meta = json.loads(_job_row(system_db, job)["meta_json"])
    assert meta["rejected"] == [
        {
            "url": "https://demo.example.com/a/2/",
            "title": "无关内容乙",
            "reason": "keyword_no_hit",
            "basis": {
                "keywords": ["民生"],
                "scope": "title+content",
                "body_len": len("中新网完整正文内容。" * 100),
            },
        }
    ], "正文到手后仍未命中 ⇒ 拒收,且留痕必须带上判定时的正文长度"


def test_body_enrichment_budget_still_capped_after_gate_move(tmp_path, monkeypatch, system_db, capture_insert):
    """R56 继承旧 P20 的预算保护意图:闸门后移不得烧穿抓取预算(负责人放行的条件)。

    钉死两道仍然排在补全之前的边界:
    ①``max_articles`` 封顶排在补全前——feed 4 条,只有前 3 条进入补全环节(a/4 绝不抓);
    ②``max_item_pages`` 封顶不变——候选 3 条、预算 2,只抓 2 个文章页(a/3 不抓)。
    旧 P20 的"闸门先于补全"不再承载这个保护,由这两道边界接手,本测试即其回归。
    """
    write_sources(
        tmp_path,
        monkeypatch,
        [rss_entry(fetch_item_pages=True, max_item_pages=2, max_articles=3,
                   item_content_selectors=["div.left_zw"])],
    )
    body_html = "<html><body><div class='left_zw'>" + "中新网完整正文内容。" * 100 + "</div></body></html>"
    pages: dict[str, object] = {
        FEED_URL: SimpleNamespace(text=feed_xml(("民生甲", "民生乙", "无关丙", "无关丁")))
    }
    for index in (1, 2, 3, 4):
        pages[f"https://demo.example.com/a/{index}/"] = _real_page(body_html)
    recorder = FetchRecorder(pages)
    monkeypatch.setattr("ttxt.crawler.fetcher.fetch_page", recorder)
    monkeypatch.setattr(robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules())
    store.create_crawl_job(
        "fake_rss", "demo", db_path=system_db, meta={"keyword_filter": ["民生"], "keywords": ["民生"]}
    )

    results = runner.run_queued(limit=1)
    assert results and results[0]["ok"] is True
    assert results[0]["feed_total"] == 4, "封顶之前的订阅源真实条数必须照实记"
    assert recorder.calls == [FEED_URL, "https://demo.example.com/a/1/", "https://demo.example.com/a/2/"], (
        "a/4 被 max_articles 挡在补全之前、a/3 被 max_item_pages 封顶挡下——"
        "闸门后移后这两道边界仍然不得突破"
    )
    assert results[0]["item_pages_capped"] == 2, (
        "候选 3 条、预算 2 ⇒ 封顶观察值记录预算值本身(既有口径,不许改动)"
    )
    assert results[0]["keyword_filtered_out"] == 1, "被配额截断的丁不算闸门拒收,丙才是"
    assert results[0]["keyword_hits"] == {"民生": {"count": 2, "in_title": 2, "in_content": 0}}
    assert [item.title for item in capture_insert[0]["items"]] == ["民生甲", "民生乙"]


def test_intake_length_gate_rejects_and_traces(tmp_path, monkeypatch, system_db, capture_insert):
    """R56 正文长度拒收线:低于 ``cleaner.MIN_INTAKE_BODY_CHARS`` 不进正式 articles,
    逐条留痕(URL/标题/拒因/判定依据)。无 keyword_filter 的例行 job 同样生效(规则 63④)。
    """
    write_sources(tmp_path, monkeypatch, [rss_entry()])
    xml = (
        "<?xml version='1.0'?><rss version='2.0'><channel>"
        "<item><title>完整报道甲</title><link>https://demo.example.com/a/1/</link>"
        f"<description>{BODY_FILLER}</description>"
        "<pubDate>Wed, 15 Nov 2026 10:00:01 +0800</pubDate></item>"
        "<item><title>残缺短文乙</title><link>https://demo.example.com/a/2/</link>"
        "<description>只有一句</description>"
        "<pubDate>Wed, 15 Nov 2026 10:00:02 +0800</pubDate></item>"
        "</channel></rss>"
    )
    recorder = FetchRecorder({FEED_URL: SimpleNamespace(text=xml)})
    monkeypatch.setattr("ttxt.crawler.fetcher.fetch_page", recorder)
    monkeypatch.setattr(robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules())
    job = store.create_crawl_job("fake_rss", "demo", db_path=system_db)

    results = runner.run_queued(limit=1)
    assert results and results[0]["ok"] is True
    assert results[0]["body_rejected"] == 1
    assert [item.title for item in capture_insert[0]["items"]] == ["完整报道甲"]
    meta = json.loads(_job_row(system_db, job)["meta_json"])
    assert meta["rejected"] == [
        {
            "url": "https://demo.example.com/a/2/",
            "title": "残缺短文乙",
            "reason": "body_below_intake_line",
            "basis": {"body_len": len("只有一句"), "threshold": cleaner.MIN_INTAKE_BODY_CHARS},
        }
    ], "长度闸拒收必须留痕到可复查:URL、标题、拒因、判定依据一个不能少"
    assert results[0].get("keyword_filtered_out", 0) == 0
    assert "keyword_hits" not in results[0], "没配关键词闸就不该出现命中统计"


def test_item_page_robots_denial_skips_without_fetch(tmp_path, monkeypatch, system_db, capture_insert):
    """文章页 robots 不放行 ⇒ 跳过 + 计数,绝不换 UA、绝不硬试。"""
    write_sources(
        tmp_path,
        monkeypatch,
        [rss_entry(fetch_item_pages=True, max_item_pages=5, item_content_selectors=["div.left_zw"])],
    )
    recorder = FetchRecorder({FEED_URL: SimpleNamespace(text=feed_xml())})
    monkeypatch.setattr("ttxt.crawler.fetcher.fetch_page", recorder)
    monkeypatch.setattr(robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules(disallow=("/a/",)))
    store.create_crawl_job("fake_rss", "demo", db_path=system_db)

    results = runner.run_queued(limit=1)
    assert results[0]["item_robots_skipped"] == 2
    assert recorder.calls == [FEED_URL]
    assert capture_insert[0]["items"][0].content == BODY_FILLER + "1", "补不到正文必须保留原摘要(填充词级,仍能过拒收线)"


def test_item_pages_all_failed_requeues_not_done(tmp_path, monkeypatch, system_db, capture_insert):
    """文章页全部失败且原本只有摘要 ⇒ 走可重试通道,不能算完成(否则静默降级)。"""
    write_sources(
        tmp_path,
        monkeypatch,
        [rss_entry(fetch_item_pages=True, max_item_pages=5, item_content_selectors=["div.left_zw"])],
    )
    recorder = FetchRecorder({FEED_URL: SimpleNamespace(text=feed_xml(descriptions=("短摘要1", "短摘要2"))), ARTICLE_URL: RuntimeError("connection reset")})
    monkeypatch.setattr("ttxt.crawler.fetcher.fetch_page", recorder)
    monkeypatch.setattr(robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules())
    store.create_crawl_job("fake_rss", "demo", db_path=system_db)

    results = runner.run_queued(limit=1)
    assert results and results[0]["ok"] is False
    assert results[0].get("item_pages_all_failed") is True
    assert "正文补全全部失败" in results[0]["error"]
    assert capture_insert == [], "全损时不得把摘要级语料当正文入库"


# ---------------------------------------------------------------- 关键词计划


@pytest.fixture
def social_category_db(tmp_path, monkeypatch):
    from ttxt import db as ttxt_db

    categories = tmp_path / "01_categories"
    (categories / "social").mkdir(parents=True)
    monkeypatch.setattr(cfg, "CATEGORIES_DIR", categories)
    monkeypatch.setattr(ttxt_db, "CATEGORIES_DIR", categories)
    path = categories / "social" / "articles.db"
    store.migrate_articles_db("social", db_path=path)
    con = db.connect(path)
    try:
        # 这个夹具测的是"已有语料时关键词闸门该生效",所以先放一条文章;
        # 空类目(冷启动)的行为由 test_plan_skips_keyword_gate_on_cold_category 单独钉。
        con.execute(
            "INSERT INTO articles(category, source, title, content, url, url_normalized, fetched_at, content_hash)"
            " VALUES('social','src_existing','已有文章','已有正文内容够长一段。','https://demo.example.com/old/',"
            "'https://demo.example.com/old/','2026-11-14T00:00:00+08:00','hash-existing-1')",
        )
        for keyword, status in (("医保报销", "gap"), ("养老退休金", "active"), ("校园餐", "active")):
            con.execute(
                "INSERT INTO keywords(keyword, category, origin, status, created_at) VALUES(?,?,?,?,?)",
                (keyword, "social", "seed", status, "2026-11-15T00:00:00+08:00"),
            )
        rows = con.execute("SELECT id, keyword FROM keywords").fetchall()
        by_keyword = {row["keyword"]: row["id"] for row in rows}
        con.execute(
            "INSERT INTO keyword_performance(keyword_id, collected, quality_articles, avg_text_length, score, updated_at)"
            " VALUES(?,?,?,?,?,?)",
            (by_keyword["养老退休金"], 12, 5, 900, 88.0, "2026-11-15T00:00:00+08:00"),
        )
        con.execute(
            "INSERT INTO keyword_performance(keyword_id, collected, quality_articles, avg_text_length, score, updated_at)"
            " VALUES(?,?,?,?,?,?)",
            (by_keyword["校园餐"], 4, 1, 300, 21.0, "2026-11-15T00:00:00+08:00"),
        )
        con.commit()
    finally:
        con.close()
    return path


def test_category_keywords_prefers_gap_then_score(social_category_db):
    rows = keyword_crawl.category_keywords("social", db_path=social_category_db, limit=3)
    assert [row["keyword"] for row in rows] == ["医保报销", "养老退休金", "校园餐"]
    assert rows[0]["status"] == "gap" and rows[1]["score"] == 88.0


def test_category_keywords_missing_db_returns_empty(tmp_path):
    assert keyword_crawl.category_keywords("nope", db_path=tmp_path / "nope.db") == []


def _plan_sources(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"sources": entries}, ensure_ascii=False), encoding="utf-8")
    return path


def test_plan_uses_keyword_filter_when_no_template(tmp_path, social_category_db, monkeypatch):
    monkeypatch.setattr(robots, "gate", lambda url, ua, cache: (FakeRules(), True))
    sources_path = _plan_sources(tmp_path, [formal_entry(id="src_social")])
    plan = keyword_crawl.plan_category("social", sources_path=sources_path, db_path=social_category_db)
    assert len(plan["plans"]) == 1
    entry = plan["plans"][0]
    assert entry["reason"] == "keyword_filter"
    assert entry["keyword_filter"] == ["医保报销", "养老退休金", "校园餐"]
    assert "url_override" not in entry


def test_plan_skips_keyword_gate_on_cold_category(tmp_path, monkeypatch):
    """空类目(social/history 现状:0 行)不得开关键词闸门:闸门会把冷启动语料卡成 0 条。"""
    from ttxt import config as cfg
    from ttxt import db as ttxt_db

    categories = tmp_path / "01_categories"
    (categories / "social").mkdir(parents=True)
    monkeypatch.setattr(cfg, "CATEGORIES_DIR", categories)
    monkeypatch.setattr(ttxt_db, "CATEGORIES_DIR", categories)
    empty_db = categories / "social" / "articles.db"
    store.migrate_articles_db("social", db_path=empty_db)
    con = db.connect(empty_db)
    try:
        for keyword in ("医保报销", "养老退休金"):
            con.execute(
                "INSERT INTO keywords(keyword, category, origin, status, created_at) VALUES(?,?,?,?,?)",
                (keyword, "social", "seed", "gap", "2026-11-15T00:00:00+08:00"),
            )
        con.commit()
    finally:
        con.close()
    monkeypatch.setattr(robots, "gate", lambda url, ua, cache: (FakeRules(), True))
    sources_path = _plan_sources(tmp_path, [formal_entry(id="src_social")])

    plan = keyword_crawl.plan_category("social", sources_path=sources_path, db_path=empty_db)
    assert plan["cold_start"] is True and plan["articles_before"] == 0
    entry = plan["plans"][0]
    assert entry["reason"] == "cold_start" and "keyword_filter" not in entry
    assert entry["keywords"], "冷启动也要记录本轮关键词,便于复盘这一轮采回来的是什么语料"


def test_plan_one_job_per_keyword_for_search_template(tmp_path, social_category_db, monkeypatch):
    monkeypatch.setattr(robots, "gate", lambda url, ua, cache: (FakeRules(), True))
    entry = formal_entry(
        id="src_search",
        url_template="https://demo.example.com/search.xml?q={keyword}",
    )
    sources_path = _plan_sources(tmp_path, [entry])
    plan = keyword_crawl.plan_category(
        "social", sources_path=sources_path, db_path=social_category_db, keyword_limit=2
    )
    planned = [item for item in plan["plans"] if item["reason"] == "keyword_search"]
    assert len(planned) == 2
    assert all("{keyword}" not in item["url_override"] for item in planned)
    assert planned[0]["url_override"].startswith("https://demo.example.com/search.xml?q=")


def test_plan_rejects_cross_host_and_robots_denied_template(tmp_path, social_category_db, monkeypatch):
    calls: list[str] = []

    def gate(url, ua, cache):
        calls.append(url)
        # demo.example 这个检索地址 robots 不放行;跨 host 的地址根本不该走到 robots
        return FakeRules(), "demo.example" not in url

    monkeypatch.setattr(robots, "gate", gate)
    sources_path = _plan_sources(
        tmp_path,
        [
            formal_entry(id="src_cross", url="https://demo.example.com/feed.xml",
                         url_template="https://other.example.com/search.xml?q={keyword}"),
            formal_entry(id="src_denied",
                         url_template="https://demo.example.com/search.xml?q={keyword}"),
        ],
    )
    plan = keyword_crawl.plan_category("social", sources_path=sources_path, db_path=social_category_db, keyword_limit=1)
    assert plan["plans"] == []
    reasons = {item["source"]: item["reason"] for item in plan["rejected"]}
    assert reasons == {"src_cross": "cross_host", "src_denied": "robots_disallow"}
    assert all("other.example" not in url for url in calls), "跨 host 计划先拒绝,不该去读它的 robots.txt"


def test_plan_and_enqueue_dedupes_and_dry_run_by_default(tmp_path, social_category_db, system_db, monkeypatch):
    monkeypatch.setattr(robots, "gate", lambda url, ua, cache: (FakeRules(), True))
    sources_path = _plan_sources(tmp_path, [formal_entry(id="src_social")])
    evidence = tmp_path / "evidence"

    dry = keyword_crawl.plan_and_enqueue(
        "social", sources_path=sources_path, db_path=social_category_db,
        system_db=system_db, evidence_dir=evidence, keyword_limit=2,
    )
    assert dry["applied"] is False and "queued_jobs" not in dry
    assert _job_count(system_db) == 0, "默认干跑不得写任务表"
    assert Path(dry["evidence_file"]).exists() and Path(dry["evidence_file"]).parent == evidence

    first = keyword_crawl.plan_and_enqueue(
        "social", sources_path=sources_path, db_path=social_category_db,
        system_db=system_db, evidence_dir=evidence, keyword_limit=2, apply=True,
    )
    assert first["queued_jobs"] == 1 and _job_count(system_db) == 1

    second = keyword_crawl.plan_and_enqueue(
        "social", sources_path=sources_path, db_path=social_category_db,
        system_db=system_db, evidence_dir=evidence, keyword_limit=2, apply=True,
    )
    assert second["queued_jobs"] == 0 and second["reused_jobs"] == 1
    assert _job_count(system_db) == 1, "同一来源+类目已有排队任务时不得重复入队"


def _job_count(db_path: Path) -> int:
    con = sqlite3.connect(db_path)
    try:
        return int(con.execute("SELECT COUNT(*) FROM crawl_jobs").fetchone()[0])
    finally:
        con.close()


def test_enqueued_params_survive_into_execution(tmp_path, social_category_db, system_db, capture_insert, monkeypatch):
    """计划入队 → runner 执行:整条链路必须把关键词带到执行现场(闭环成立)。"""
    monkeypatch.setattr(robots, "gate", lambda url, ua, cache: (FakeRules(), True))
    sources_path = _plan_sources(tmp_path, [formal_entry(id="fake_rss", category="demo")])
    # 计划与执行都用 demo 类目/sources.json,验证 meta → crawl_job_params → 关键词闸门
    write_sources(tmp_path, monkeypatch, [formal_entry(id="fake_rss", category="demo")])
    plan = keyword_crawl.plan_and_enqueue(
        "demo",
        keywords=["民生"],
        sources_path=sources_path,
        db_path=social_category_db,
        system_db=system_db,
        evidence_dir=tmp_path / "evidence",
        apply=True,
    )
    assert plan["queued_jobs"] == 1
    recorder = FetchRecorder({FEED_URL: SimpleNamespace(text=feed_xml())})
    monkeypatch.setattr("ttxt.crawler.fetcher.fetch_page", recorder)
    monkeypatch.setattr(robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules())

    results = runner.run_queued(limit=1)
    assert results and results[0]["ok"] is True
    assert results[0]["keyword_hits"] == {"民生": {"count": 1, "in_title": 1, "in_content": 0}}
    assert results[0]["keyword_filtered_out"] == 1
    assert [item.title for item in capture_insert[0]["items"]] == ["民生新闻甲"]
