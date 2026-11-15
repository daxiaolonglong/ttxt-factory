"""P21 公开页互动指标采集(头条文章页点赞/评论)与指标合并诚实性测试。

取证依据(07_tmp/r24_metrics_probe/probe1.json、probe2_render.json、probe3_selectors.json):
- 头条 robots.txt 不禁止 /article/;纯 HTTP 抓到的是字节跳动 JSVMP 反爬壳 ⇒ 必须走正常浏览器渲染;
- 渲染页里**本文章**只有点赞(`.detail-side-interaction .detail-like[aria-label="点赞N"]`)与
  评论(`.detail-interaction-comment[aria-label="…N评论"]`、`#comment-area .title`);
- 阅读数只出现在"推荐阅读"卡片(**别的文章**的),收藏数与展现量公开页根本没有。

必须全部有断言覆盖的红线:
1) 卡片/评论区别人的数字绝不许安到本文章头上(跨卡片归属 = 编造指标);
2) 取不到的字段写 NULL,绝不折算 0;页面里的真 0 必须如实写 0;
3) 主机不在白名单、robots 不放行 ⇒ 一次抓取都不许发生;
4) 默认 dry-run 不落库;落库时同时写 article_metrics 现值与 metric_snapshots 快照,并带来源与可信度;
5) 弱可信度来源绝不覆盖强可信度来源已有数值;NULL 绝不覆盖已知数值;同可信度取较新非空值;
6) 未授权来源一律 PermissionError(公开页来源也必须先白名单登记)。
"""

from __future__ import annotations

import sqlite3

import pytest
from scrapling.parser import Selector

from ttxt.analysis import metrics_import, viral
from ttxt.crawler import metrics_pages, store
from ttxt.crawler.extract import ExtractedItem
from ttxt.crawler.robots import RobotsRules

# ---------------------------------------------------------------------------
# 真实 DOM 片段(从 07_tmp/r24_metrics_probe/pages/*.render.html 原样截取)

TOOLBAR = """
<div role="complementary" aria-label="工具栏" class="detail-side-interaction">
  <div tabindex="0" role="button" aria-label="点赞437" aria-pressed="false" class="detail-like">
    <div class="digg-icon"><div class="inner"><i></i></div></div><span>437</span>
  </div>
  <div class="divide"></div>
  <div tabindex="0" role="button" aria-label="打开评论面板，47评论" class="detail-interaction-comment">
    <span>47</span>
  </div>
</div>
"""

COMMENT_BLOCK = """
<div class="ttp-comment-block comment-folded"><div id="comment-area" class="ttp-comment-wrapper">
  <div class="title">评论 <span>47</span></div>
  <ul class="comment-list"><li><div class="ttp-comment-item">
    <div class="right-action"><div tabindex="0" role="button" aria-label="点赞7" aria-pressed="false"
      class="ttp-comment-like icon-default"><div class="inner"><span class="">7</span></div></div></div>
  </div></li></ul>
</div></div>
"""

RELATED_CARDS = """
<ul class="related-list"><li class="related-list-item"><div class="right-content">
  <a class="title" href="/article/7701987817991488034/">张一鸣又造出一个国民级APP</a>
  <p class="info"><span>16万阅读</span><span>10月29日</span></p>
</div></li></ul>
<div class="left-tools"><div class="feed-card-footer-comment-cmp"><a>1628评论</a></div></div>
"""

ARTICLE_META = '<div class="article-meta"><span>2026-11-12 11:17</span> · <span>人人都是产品经理</span></div>'


def html_of(*blocks: str) -> str:
    return "<html><body>" + "".join(blocks) + "</body></html>"


def page(*blocks: str) -> Selector:
    return Selector(html_of(*blocks))


def article_page() -> Selector:
    return page(ARTICLE_META, TOOLBAR, COMMENT_BLOCK, RELATED_CARDS)


def metrics_of(sel: Selector) -> dict:
    return metrics_pages.extract_public_metrics(sel)


# ---------------------------------------------------------------------------
# 一、抽取归属:只允许本文章工具栏的数字

def test_only_own_toolbar_counts_are_taken():
    got = metrics_of(article_page())
    assert got["likes"] == 437
    assert got["comments"] == 47
    assert got["reads"] is None
    assert got["favorites"] is None
    assert got["impressions"] is None


def test_card_numbers_are_never_attributed_to_the_article():
    """只有推荐卡片时(没有本文章工具栏、没有本文章评论区),"16万阅读""1628评论" 一律不算本文章指标。"""
    got = metrics_of(page(ARTICLE_META, RELATED_CARDS))
    assert all(value is None for value in got.values()), got


def test_own_comment_section_title_counts_as_our_comments():
    """本文章评论区标题 "评论 47" 是本文章的评论数;卡片 "1628评论" 与评论点赞 7 不算。"""
    got = metrics_of(page(ARTICLE_META, COMMENT_BLOCK, RELATED_CARDS))
    assert got["comments"] == 47
    assert got["likes"] is None and got["reads"] is None


def test_comment_like_counts_do_not_leak_into_article_likes():
    sel = page(
        '<div role="complementary" aria-label="工具栏" class="detail-side-interaction">'
        '<div class="detail-like"><span>赞</span></div>'
        '<div class="detail-interaction-comment"><span>9</span></div></div>',
        COMMENT_BLOCK,
    )
    got = metrics_of(sel)
    assert got["likes"] is None      # 无数字不许推断
    assert got["comments"] == 9


def test_comment_count_falls_back_to_comment_section_title():
    sel = page(
        '<div role="complementary" class="detail-side-interaction">'
        '<div class="detail-like"><span>23</span></div></div>',
        '<div id="comment-area"><div class="title">评论 <span>5</span></div></div>',
    )
    got = metrics_of(sel)
    assert got["likes"] == 23
    assert got["comments"] == 5


def test_wan_and_comma_numbers_parse():
    sel = page(
        '<div role="complementary" class="detail-side-interaction">'
        '<div class="detail-like" aria-label="点赞1.2万"><span>1.2万</span></div>'
        '<div class="detail-interaction-comment" aria-label="打开评论面板，1,234评论"><span>1,234</span></div></div>'
    )
    got = metrics_of(sel)
    assert got["likes"] == 12000
    assert got["comments"] == 1234


def test_aria_label_without_number_is_none():
    sel = page(
        '<div role="complementary" class="detail-side-interaction">'
        '<div class="detail-like" aria-label="点赞"><span></span></div>'
        '<div class="detail-interaction-comment" aria-label="打开评论面板"><span></span></div></div>'
    )
    got = metrics_of(sel)
    assert got["likes"] is None and got["comments"] is None


def test_real_zero_is_written_as_zero_not_none():
    sel = page(
        '<div role="complementary" class="detail-side-interaction">'
        '<div class="detail-like" aria-label="点赞0"><span>0</span></div>'
        '<div class="detail-interaction-comment" aria-label="打开评论面板，0评论"><span>0</span></div></div>'
    )
    got = metrics_of(sel)
    assert got["likes"] == 0 and got["comments"] == 0


def test_parse_count_text_rejects_garbage():
    assert metrics_pages.parse_count_text("16万") == 160000
    assert metrics_pages.parse_count_text("2,724") == 2724
    assert metrics_pages.parse_count_text("") is None
    assert metrics_pages.parse_count_text("赞") is None
    assert metrics_pages.parse_count_text("-5") is None
    assert metrics_pages.parse_count_text("1.2万") == 12000


# ---------------------------------------------------------------------------
# 二、主机与 robots 闸门:不放行就不许发起一次抓取

def test_host_whitelist():
    assert metrics_pages.host_allowed("https://www.toutiao.com/article/1/")
    assert metrics_pages.host_allowed("https://m.toutiao.com/article/1/")
    assert not metrics_pages.host_allowed("https://www.chinanews.com.cn/rss/society.xml")
    assert not metrics_pages.host_allowed("https://evil-toutiao.com.example.net/article/1/")


def _item(title: str, content: str, url: str) -> ExtractedItem:
    return ExtractedItem(title=title, content=content, excerpt="", author=None, published_at=None, url=url)


def _demo_db(tmp_path, urls: list[str]):
    db_path = store.init_articles_db("demo", db_path=tmp_path / "demo.db")
    rows = [
        _item(f"第 {i} 篇测试文章标题够长了吧", "正文" * 300 + f" 第 {i} 篇", url)
        for i, url in enumerate(urls, start=1)
    ]
    store.insert_items("demo", "test", rows, db_path=db_path)
    store.migrate_articles_db("demo", db_path=db_path)
    return db_path


TOUTIAO_URLS = [
    "https://www.toutiao.com/article/7707118928425763355/",
    "https://www.toutiao.com/article/7707526791782613519/",
]
AUTHORIZED = {metrics_pages.SOURCE_ID: "public_page", "manual_verified": "verified", "toutiao_admin_export": "self_reported"}


def _fake_fetch(pages: dict[str, str], calls: list[str]):
    def fetch(url: str):
        calls.append(url)
        return Selector(pages[url])

    return fetch


def test_non_whitelisted_host_rows_are_never_fetched(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS + ["https://www.chinanews.com.cn/news/2026/abc.html"])
    calls: list[str] = []
    report = metrics_pages.collect_public_metrics(
        "demo",
        db_path=db_path,
        apply=False,
        fetch_page=_fake_fetch({url: TOOLBAR for url in TOUTIAO_URLS}, calls),
        authorized=AUTHORIZED,
        sleeper=lambda _s: None,
        rules_provider=lambda _base: RobotsRules(allow_all=True),
    )
    assert sorted(calls) == sorted(TOUTIAO_URLS)
    assert report["candidates"] == 2
    assert report["host_skipped"] == 1


def test_robots_unknown_refuses_every_url(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS)
    calls: list[str] = []
    report = metrics_pages.collect_public_metrics(
        "demo",
        db_path=db_path,
        apply=False,
        fetch_page=_fake_fetch({url: TOOLBAR for url in TOUTIAO_URLS}, calls),
        authorized=AUTHORIZED,
        sleeper=lambda _s: None,
        rules_provider=lambda _base: RobotsRules(allow_all=False, unknown=True),
    )
    assert calls == []
    assert report["robots_refused"] == 2
    assert report["fetched"] == 0


def test_robots_disallow_specific_path_skips_that_url(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS)
    calls: list[str] = []
    rules = RobotsRules(allow_all=False, rules=[(False, "/article/7707526791782613519")])
    report = metrics_pages.collect_public_metrics(
        "demo",
        db_path=db_path,
        apply=False,
        fetch_page=_fake_fetch({TOUTIAO_URLS[0]: TOOLBAR}, calls),
        authorized=AUTHORIZED,
        sleeper=lambda _s: None,
        rules_provider=lambda _base: rules,
    )
    assert calls == [TOUTIAO_URLS[0]]
    assert report["robots_refused"] == 1


# ---------------------------------------------------------------------------
# 三、写库诚实性:dry-run、缺失 NULL、真 0 落 0、快照与溯源

def _metrics_row(db_path, article_id: int) -> dict:
    con = sqlite3.connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM article_metrics WHERE article_id=?", (article_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        con.close()


def _snapshot_rows(db_path) -> list[dict]:
    con = sqlite3.connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute("SELECT * FROM metric_snapshots ORDER BY id")]
    finally:
        con.close()


def _pages_map(html: str) -> dict[str, str]:
    return {url: html for url in TOUTIAO_URLS}


def test_dry_run_writes_nothing(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS)
    report = metrics_pages.collect_public_metrics(
        "demo",
        db_path=db_path,
        apply=False,
        fetch_page=_fake_fetch(_pages_map(html_of(TOOLBAR, COMMENT_BLOCK)), []),
        authorized=AUTHORIZED,
        sleeper=lambda _s: None,
        rules_provider=lambda _base: RobotsRules(allow_all=True),
    )
    assert report["dry_run"] is True
    assert report["written"] == 0
    assert _metrics_row(db_path, 1) == {}


def test_apply_writes_metrics_snapshots_and_provenance(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS)
    html = html_of(ARTICLE_META, TOOLBAR, COMMENT_BLOCK, RELATED_CARDS)
    report = metrics_pages.collect_public_metrics(
        "demo",
        db_path=db_path,
        apply=True,
        fetch_page=_fake_fetch(_pages_map(html), []),
        authorized=AUTHORIZED,
        sleeper=lambda _s: None,
        rules_provider=lambda _base: RobotsRules(allow_all=True),
        captured_at="2026-11-15T17:00:00",
    )
    assert report["written"] == 2 and report["snapshots"] == 2
    row = _metrics_row(db_path, 1)
    assert row["likes"] == 437 and row["comments"] == 47
    assert row["reads"] is None and row["favorites"] is None and row["impressions"] is None
    assert row["data_source"] == metrics_pages.SOURCE_ID
    assert row["credibility"] == "public_page"
    assert row["captured_at"] == "2026-11-15T17:00:00"
    snaps = _snapshot_rows(db_path)
    assert len(snaps) == 2 and {s["data_source"] for s in snaps} == {metrics_pages.SOURCE_ID}


def test_all_null_page_writes_nothing(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS)
    html = html_of(ARTICLE_META, RELATED_CARDS)  # 只有卡片数字 ⇒ 本文章什么都没测到
    report = metrics_pages.collect_public_metrics(
        "demo",
        db_path=db_path,
        apply=True,
        fetch_page=_fake_fetch(_pages_map(html), []),
        authorized=AUTHORIZED,
        sleeper=lambda _s: None,
        rules_provider=lambda _base: RobotsRules(allow_all=True),
    )
    assert report["written"] == 0 and report["snapshots"] == 0
    assert report["no_metrics_skipped"] == 2
    assert _metrics_row(db_path, 1) == {}


def test_unauthorized_public_source_is_refused(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS)
    html = html_of(ARTICLE_META, TOOLBAR, COMMENT_BLOCK)
    with pytest.raises(PermissionError):
        metrics_pages.collect_public_metrics(
            "demo",
            db_path=db_path,
            apply=True,
            fetch_page=_fake_fetch(_pages_map(html), []),
            authorized={"manual_verified": "verified"},
            sleeper=lambda _s: None,
            rules_provider=lambda _base: RobotsRules(allow_all=True),
        )


def test_repeat_fetch_updates_value_and_appends_snapshot(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:1])
    first = html_of(ARTICLE_META, TOOLBAR, COMMENT_BLOCK)
    second = html_of(
        '<div role="complementary" class="detail-side-interaction">'
        '<div class="detail-like" aria-label="点赞500"><span>500</span></div>'
        '<div class="detail-interaction-comment" aria-label="打开评论面板，60评论"><span>60</span></div></div>'
    )
    for html in (first, second):
        metrics_pages.collect_public_metrics(
            "demo",
            db_path=db_path,
            apply=True,
            fetch_page=_fake_fetch({TOUTIAO_URLS[0]: html}, []),
            authorized=AUTHORIZED,
            sleeper=lambda _s: None,
            rules_provider=lambda _base: RobotsRules(allow_all=True),
        )
    row = _metrics_row(db_path, 1)
    assert row["likes"] == 500 and row["comments"] == 60
    assert len(_snapshot_rows(db_path)) == 2


def test_public_metrics_move_viral_tier_to_no_impressions(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:1])
    html = html_of(ARTICLE_META, TOOLBAR, COMMENT_BLOCK)
    metrics_pages.collect_public_metrics(
        "demo",
        db_path=db_path,
        apply=True,
        fetch_page=_fake_fetch({TOUTIAO_URLS[0]: html}, []),
        authorized=AUTHORIZED,
        sleeper=lambda _s: None,
        rules_provider=lambda _base: RobotsRules(allow_all=True),
    )
    report = metrics_import.relabel_after_import("demo", db_path=db_path)
    assert report["tiers"].get("no_impressions", 0) >= 1
    con = sqlite3.connect(db_path)
    try:
        tier, label = con.execute(
            "SELECT confidence_tier, label FROM viral_labels WHERE article_id=1"
        ).fetchone()
    finally:
        con.close()
    assert tier == viral.TIER_NO_IMPRESSIONS
    assert label == viral.LABEL_UNSCORED


# ---------------------------------------------------------------------------
# 四、可信度合并:弱来源不覆盖强来源,NULL 不覆盖已知值

def _import_csv(tmp_path, name: str, text: str):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_public_page_does_not_clobber_admin_export(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:1])
    admin = _import_csv(
        tmp_path,
        "admin.csv",
        "article_id,reads,likes,favorites,comments,impressions\n1,900000,100,2000,30,4000000\n",
    )
    r1 = metrics_import.import_metrics(
        "demo", admin, "toutiao_admin_export", db_path=db_path, authorized=AUTHORIZED
    )
    assert r1["written"] == 1
    public = _import_csv(tmp_path, "public.csv", "article_id,likes,comments\n1,437,47\n")
    r2 = metrics_import.import_metrics(
        "demo", public, metrics_pages.SOURCE_ID, db_path=db_path, authorized=AUTHORIZED
    )
    row = _metrics_row(db_path, 1)
    assert row["reads"] == 900000 and row["impressions"] == 4000000
    assert row["favorites"] == 2000
    assert row["likes"] == 100 and row["comments"] == 30      # 强来源数值保住
    assert r2["protected_by_credibility"] == 2
    assert row["credibility"] == "self_reported"


def test_admin_export_fills_gaps_left_by_public_page(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:1])
    public = _import_csv(tmp_path, "public.csv", "article_id,likes,comments\n1,437,47\n")
    metrics_import.import_metrics("demo", public, metrics_pages.SOURCE_ID, db_path=db_path, authorized=AUTHORIZED)
    admin = _import_csv(tmp_path, "admin.csv", "article_id,reads,impressions\n1,900000,4000000\n")
    r = metrics_import.import_metrics("demo", admin, "toutiao_admin_export", db_path=db_path, authorized=AUTHORIZED)
    row = _metrics_row(db_path, 1)
    assert (row["likes"], row["comments"]) == (437, 47)        # 缺口反过来补上,不丢
    assert (row["reads"], row["impressions"]) == (900000, 4000000)
    assert row["credibility"] == "self_reported"
    assert r["filled_from_null"] == 2


def test_same_credibility_newer_non_null_wins_and_null_keeps(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:1])
    first = _import_csv(tmp_path, "a.csv", "article_id,likes,comments\n1,10,5\n")
    metrics_import.import_metrics("demo", first, metrics_pages.SOURCE_ID, db_path=db_path, authorized=AUTHORIZED)
    second = _import_csv(tmp_path, "b.csv", "article_id,likes,comments\n1,20,\n")
    metrics_import.import_metrics("demo", second, metrics_pages.SOURCE_ID, db_path=db_path, authorized=AUTHORIZED)
    row = _metrics_row(db_path, 1)
    assert row["likes"] == 20      # 同可信度:较新非空覆盖
    assert row["comments"] == 5    # 缺失不许清空已知值


def test_all_null_import_still_rejected(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:1])
    empty = _import_csv(tmp_path, "e.csv", "article_id,likes,comments\n1,,\n")
    r = metrics_import.import_metrics("demo", empty, metrics_pages.SOURCE_ID, db_path=db_path, authorized=AUTHORIZED)
    assert r["written"] == 0 and r["row_errors"]
    assert _metrics_row(db_path, 1) == {}


def test_credibility_rank_ordering():
    assert metrics_import.credibility_rank("verified") > metrics_import.credibility_rank("self_reported")
    assert metrics_import.credibility_rank("self_reported") > metrics_import.credibility_rank("public_page")
    assert metrics_import.credibility_rank("public_page") > metrics_import.credibility_rank("unverified")
    assert metrics_import.credibility_rank("没见过的来源") == 0


def test_config_whitelist_registers_public_page_source():
    allow = metrics_import.load_authorized_sources()
    assert metrics_pages.SOURCE_ID in allow
    assert allow[metrics_pages.SOURCE_ID] == "public_page"


# ---------------------------------------------------------------------------
# 五、命令行接线:默认 dry-run,只有 --apply 才写库

def test_cli_fetch_public_defaults_to_dry_run(monkeypatch, capsys):
    from ttxt import __main__ as cli

    seen: dict = {}

    def fake_collect(category, db_path=None, limit=None, apply=False, **kwargs):  # noqa: A002
        seen["category"] = category
        seen["limit"] = limit
        seen["apply"] = apply
        seen["sleep_s"] = kwargs.get("sleep_s")
        return {"category": category, "candidates": 2, "fetched": 2, "written": 0,
                "dry_run": True, "pages": [], "row_errors": []}

    monkeypatch.setattr(cli.crawler_metrics_pages, "collect_with_browser", fake_collect)
    assert cli.main(["metrics", "--category", "technology", "--fetch-public", "--limit", "3"]) == 0
    out = capsys.readouterr().out
    assert '"candidates": 2' in out
    assert "dry-run" in out
    assert seen["category"] == "technology" and seen["limit"] == 3
    assert seen["apply"] is False                      # 不加 --apply 绝不写库


def test_cli_fetch_public_writes_only_with_apply(monkeypatch, capsys):
    from ttxt import __main__ as cli

    seen: dict = {}

    def fake_collect(category, db_path=None, limit=None, apply=False, **kwargs):  # noqa: A002
        seen["apply"] = apply
        return {"category": category, "candidates": 1, "fetched": 1, "written": 1,
                "snapshots": 1, "dry_run": False, "pages": [], "row_errors": []}

    monkeypatch.setattr(cli.crawler_metrics_pages, "collect_with_browser", fake_collect)
    code = cli.main(["metrics", "--category", "technology", "--fetch-public", "--apply",
                     "--no-relabel", "--limit", "1", "--sleep", "0"])
    assert code == 0
    assert seen["apply"] is True
    assert '"written": 1' in capsys.readouterr().out


def test_cli_unauthorized_source_returns_error_code(monkeypatch, capsys):
    from ttxt import __main__ as cli

    def boom(*_args, **_kwargs):
        raise PermissionError("未授权数据来源")

    monkeypatch.setattr(cli.crawler_metrics_pages, "collect_with_browser", boom)
    assert cli.main(["metrics", "--category", "technology", "--fetch-public"]) == 2
    assert "未授权数据来源" in capsys.readouterr().out
