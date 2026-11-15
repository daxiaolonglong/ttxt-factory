"""P22:头条文章页内嵌 RENDER_DATA 计数(阅读/点赞/评论/展现量,精确值)采集与打分口径测试。

取证依据(离线复用 07_tmp/r24_metrics_probe/pages/*.render.html,不再联网):
- probe5_offline_keys.json:渲染页里能看到 itemCounter 的片段(readCount/showCount/repinCount…);
- probe6_render_data.json:**`<script id="RENDER_DATA">` 是 percent-encoded JSON**,
  `$.data.itemId == 页面 URL 的文章 id`(归属可证明),
  `$.data.itemCell.itemCounter = {buryCount:0, commentCount:47, diggCount:437, readCount:8937,
  shareCount:1801, showCount:111790, repinCount:1927, forwardCount:15, danmuCount:0}`,
  且 diggCount/commentCount 与工具条 aria-label(点赞437 / 47评论)完全一致 ⇒ 交叉验证通过。

红线(必须有断言):
1) 内嵌计数只有在 **payload 里的文章 id == 本页 URL 的文章 id** 时才许用;对不上 ⇒ 整块丢弃;
2) 没有 URL 上下文(无法证明归属)⇒ 内嵌计数一律不用;
3) 展现量(showCount)小于阅读数(readCount)⇒ 物理不可能 ⇒ 拒绝写 impressions,保留其余;
4) 收藏数(favorites)公开页仍然没有 ⇒ 恒为 NULL;share/repin/forward/bury/danmu 语义未证实
   ⇒ 只进报告当证据,绝不入库、绝不改名冒充收藏;
5) 真 0 写 0,取不到写 NULL,缺失不等于 0;
6) 打分:收藏结构性不可得时**不得代入 0**,只在可观测率上按权重归一化;核心互动(评论/点赞)缺失仍 incomplete_data;
7) 打分口径必须留下用了哪些字段的痕迹(features_json.metric_fields + reason)。
"""

from __future__ import annotations

import json
import sqlite3
import urllib.parse

import pytest
from scrapling.parser import Selector

from ttxt.analysis import viral
from ttxt.crawler import metrics_pages, store
from ttxt.crawler.extract import ExtractedItem
from ttxt.crawler.robots import RobotsRules

# ---------------------------------------------------------------------------
# 真实数据片段(原样取自 07_tmp/r24_metrics_probe/pages/article_7707118928425763355.render.html)

REAL_COUNTERS = {
    "buryCount": 0,
    "commentCount": 47,
    "diggCount": 437,
    "readCount": 8937,
    "shareCount": 1801,
    "showCount": 111790,
    "repinCount": 1927,
    "forwardCount": 15,
    "danmuCount": 0,
}
ARTICLE_ID = "7707118928425763355"
ARTICLE_URL = f"https://www.toutiao.com/article/{ARTICLE_ID}/"
OTHER_ID = "7707526791782613519"
OTHER_URL = f"https://www.toutiao.com/article/{OTHER_ID}/"
# 第二篇文章的真实计数(probe6_render_data.json)
OTHER_COUNTERS = {
    "buryCount": 0,
    "commentCount": 5,
    "diggCount": 23,
    "readCount": 3112,
    "shareCount": 3,
    "showCount": 186085,
    "repinCount": 12,
    "forwardCount": 1,
}

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
"""

ARTICLE_META = '<div class="article-meta"><span>2026-11-12 11:17</span> · <span>人人都是产品经理</span></div>'


def toolbar(likes: int, comments: int) -> str:
    """本文章工具条(数字可参数化,用于"每页归属各自文章"的场景)。"""
    return (
        '<div role="complementary" aria-label="工具栏" class="detail-side-interaction">'
        f'<div tabindex="0" role="button" aria-label="点赞{likes}" class="detail-like"><span>{likes}</span></div>'
        '<div class="divide"></div>'
        f'<div tabindex="0" role="button" aria-label="打开评论面板，{comments}评论" '
        f'class="detail-interaction-comment"><span>{comments}</span></div>'
        "</div>"
    )

AUTHORIZED = {
    metrics_pages.SOURCE_ID: "public_page",
    "manual_verified": "verified",
    "toutiao_admin_export": "self_reported",
}


def render_script(item_id: str | None = ARTICLE_ID, counters: dict | None = None) -> str:
    payload = {
        "data": {
            "itemId": item_id,
            "itemCell": {
                "itemCounter": dict(REAL_COUNTERS if counters is None else counters),
                "likeData": {"userLikeStatus": 0, "count": (counters or REAL_COUNTERS).get("diggCount", 437)},
            },
        }
    }
    blob = urllib.parse.quote(json.dumps(payload, ensure_ascii=False), safe="")
    return f'<script id="RENDER_DATA" type="application/json">{blob}</script>'


def html_of(*blocks: str) -> str:
    return "<html><body>" + "".join(blocks) + "</body></html>"


def page(*blocks: str) -> Selector:
    return Selector(html_of(*blocks))


def extract(*blocks: str, url: str | None = ARTICLE_URL, detail: dict | None = None) -> dict:
    return metrics_pages.extract_public_metrics(page(*blocks), url=url, detail=detail)


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


def _metrics_row(db_path, article_id: int) -> dict:
    con = sqlite3.connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM article_metrics WHERE article_id=?", (article_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        con.close()


def _fake_fetch(pages: dict[str, str], calls: list[str] | None = None):
    def fetch(url: str):
        if calls is not None:
            calls.append(url)
        return Selector(pages[url])

    return fetch


# ---------------------------------------------------------------------------
# 一、归属:内嵌计数只有 id 对得上才许用

def test_render_data_gives_exact_reads_likes_comments_impressions():
    got = extract(render_script(), TOOLBAR, COMMENT_BLOCK, RELATED_CARDS)
    assert got["reads"] == 8937, "阅读数从 readCount 精确取,不再需要卡片估算"
    assert got["likes"] == 437
    assert got["comments"] == 47
    assert got["impressions"] == 111790, "展现量在公开页就有(showCount),不需要等后台导出"
    assert got["favorites"] is None, "收藏数公开页仍然没有 ⇒ 恒为 NULL"


def test_render_data_is_used_over_dom_text_and_marked_in_detail():
    detail: dict = {}
    other = dict(REAL_COUNTERS, diggCount=500, commentCount=60)
    extract(render_script(counters=other), TOOLBAR, COMMENT_BLOCK, url=ARTICLE_URL, detail=detail)
    assert detail.get("render_used") is True
    assert detail.get("render_counters", {}).get("diggCount") == 500


def test_render_data_dropped_when_item_id_is_not_our_article():
    """payload 的 itemId 与页面 URL 的文章 id 对不上 ⇒ 整块内嵌计数不许用(防跨文章张冠李戴)。"""
    detail: dict = {}
    got = extract(render_script(item_id="7707526791782613519"), TOOLBAR, COMMENT_BLOCK, detail=detail)
    assert got["reads"] is None and got["impressions"] is None
    assert got["likes"] == 437 and got["comments"] == 47  # 仍走本文章工具条兜底
    assert detail.get("render_mismatch") == 1
    assert detail.get("render_used") is not True


def test_render_data_never_used_without_url_context():
    """没有 URL 就无法证明归属 ⇒ 内嵌计数一律不用。"""
    got = extract(render_script(), TOOLBAR, COMMENT_BLOCK, url=None)
    assert got["reads"] is None and got["impressions"] is None
    assert got["likes"] == 437 and got["comments"] == 47


def test_render_data_ignored_when_payload_has_no_id():
    detail: dict = {}
    blob = urllib.parse.quote(
        json.dumps({"data": {"itemCell": {"itemCounter": dict(REAL_COUNTERS)}}}, ensure_ascii=False), safe=""
    )
    blocks = [f'<script id="RENDER_DATA" type="application/json">{blob}</script>', TOOLBAR, COMMENT_BLOCK]
    got = extract(*blocks, detail=detail)
    assert got["reads"] is None and got["impressions"] is None
    assert got["likes"] == 437
    assert detail.get("render_unattributed", 0) >= 1


# ---------------------------------------------------------------------------
# 二、数值合理性:物理不可能的组合不许入库;真 0 不许当 NULL

def test_show_count_below_reads_is_refused_as_impossible():
    detail: dict = {}
    counters = dict(REAL_COUNTERS, readCount=9000, showCount=100)
    got = extract(render_script(counters=counters), url=ARTICLE_URL, detail=detail)
    assert got["reads"] == 9000
    assert got["impressions"] is None, "展现量 < 阅读量 物理不可能 ⇒ 不许当 impressions"
    assert detail.get("sanity_rejected", 0) >= 1


def test_real_zero_counters_are_zero_not_null():
    counters = {"commentCount": 0, "diggCount": 0, "readCount": 0, "showCount": 0}
    got = extract(render_script(counters=counters), url=ARTICLE_URL)
    assert got["likes"] == 0 and got["comments"] == 0
    assert got["reads"] == 0 and got["impressions"] == 0
    assert got["favorites"] is None


def test_broken_json_block_falls_back_to_dom_without_crash():
    detail: dict = {}
    blocks = ['<script id="RENDER_DATA" type="application/json">%7B%22data%22%3Abroken</script>', TOOLBAR, COMMENT_BLOCK]
    got = extract(*blocks, detail=detail)
    assert got["likes"] == 437 and got["comments"] == 47
    assert got["impressions"] is None
    assert detail.get("render_error")


def test_page_without_render_data_keeps_p21_behaviour():
    got = extract(TOOLBAR, COMMENT_BLOCK, RELATED_CARDS)
    assert got["likes"] == 437 and got["comments"] == 47
    assert got["reads"] is None and got["impressions"] is None


# ---------------------------------------------------------------------------
# 三、URL 里的文章 id

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.toutiao.com/article/7707118928425763355/", ARTICLE_ID),
        ("https://www.toutiao.com/article/7707118928425763355/?source=x", ARTICLE_ID),
        ("https://m.toutiao.com/a1770000000000000/", "1770000000000000"),
        ("https://www.toutiao.com/group/7707118928425763355/", ARTICLE_ID),
        ("https://www.toutiao.com/w/1234567890123456789/", "1234567890123456789"),
        ("https://www.toutiao.com/ch/news_tech/", None),
        ("", None),
    ],
)
def test_article_id_from_url(url: str, expected: str | None):
    assert metrics_pages.article_id_from_url(url) == expected


# ---------------------------------------------------------------------------
# 四、落库:证据字段不入库,只有五个指标列入库

def test_apply_stores_reads_and_impressions_with_provenance(tmp_path):
    """两篇文章各自页面的 RENDER_DATA 只喂自己的文章(逐页归属,不串行)。"""
    db_path = _demo_db(tmp_path, [ARTICLE_URL, OTHER_URL])
    pages = {
        ARTICLE_URL: html_of(ARTICLE_META, render_script(), TOOLBAR, COMMENT_BLOCK),
        OTHER_URL: html_of(
            ARTICLE_META, render_script(item_id=OTHER_ID, counters=OTHER_COUNTERS), toolbar(23, 5), COMMENT_BLOCK
        ),
    }
    report = metrics_pages.collect_public_metrics(
        "demo",
        db_path=db_path,
        apply=True,
        fetch_page=_fake_fetch(pages),
        authorized=AUTHORIZED,
        sleeper=lambda _s: None,
        rules_provider=lambda _base: RobotsRules(allow_all=True),
        captured_at="2026-11-15T17:40:00",
    )
    assert report["written"] == 2 and report["render_ok"] == 2
    assert report["render_mismatch"] == 0
    first, second = _metrics_row(db_path, 1), _metrics_row(db_path, 2)
    assert first["reads"] == 8937 and first["impressions"] == 111790
    assert first["likes"] == 437 and first["comments"] == 47
    assert first["favorites"] is None
    assert first["credibility"] == "public_page"
    assert second["reads"] == 3112 and second["impressions"] == 186085
    assert second["likes"] == 23 and second["comments"] == 5
    by_article = {entry["article_id"]: entry for entry in report["pages"]}
    assert by_article[1]["render_counters"]["shareCount"] == 1801, "分享/转发等只当证据留在报告里"
    assert by_article[2]["render_counters"]["showCount"] == 186085
    assert "shareCount" not in first and "repinCount" not in first


def test_render_counters_report_summary_counts(tmp_path):
    db_path = _demo_db(tmp_path, [ARTICLE_URL])
    wrong_id = html_of(ARTICLE_META, render_script(item_id="7707526791782613519"), TOOLBAR, COMMENT_BLOCK)
    report = metrics_pages.collect_public_metrics(
        "demo",
        db_path=db_path,
        apply=False,
        fetch_page=_fake_fetch({ARTICLE_URL: wrong_id}),
        authorized=AUTHORIZED,
        sleeper=lambda _s: None,
        rules_provider=lambda _base: RobotsRules(allow_all=True),
    )
    assert report["render_ok"] == 0
    assert report["render_mismatch"] == 1
    assert report["fetched"] == 1


def test_mismatch_page_still_writes_own_dom_counts(tmp_path):
    db_path = _demo_db(tmp_path, [ARTICLE_URL])
    wrong_id = html_of(ARTICLE_META, render_script(item_id="7707526791782613519"), TOOLBAR, COMMENT_BLOCK)
    report = metrics_pages.collect_public_metrics(
        "demo",
        db_path=db_path,
        apply=True,
        fetch_page=_fake_fetch({ARTICLE_URL: wrong_id}),
        authorized=AUTHORIZED,
        sleeper=lambda _s: None,
        rules_provider=lambda _base: RobotsRules(allow_all=True),
    )
    assert report["written"] == 1
    row = _metrics_row(db_path, 1)
    assert row["likes"] == 437 and row["comments"] == 47
    assert row["reads"] is None and row["impressions"] is None


# ---------------------------------------------------------------------------
# 五、打分口径:收藏缺失不得代入 0,只在可观测率上归一化

def _f() -> dict:
    return viral.compute_features("标题", "正文" * 40, 120)


def test_favorites_missing_renormalizes_instead_of_zero_filling():
    metrics = {"impressions": 111790, "reads": 8937, "likes": 437, "favorites": None, "comments": 47}
    label, tier, score, reason = viral.score_with_metrics(_f(), metrics)
    assert tier == viral.TIER_MEASURED and score is not None
    read_rate = 8937 / 111790
    like_rate = 437 / 8937
    comment_rate = 47 / 8937
    expected = (0.4 * read_rate + 0.2 * like_rate + 0.15 * comment_rate) / 0.75
    assert score == pytest.approx(round(expected, 4))
    assert "归一化" in reason and "收藏" in reason


def test_favorites_present_uses_full_weight_set():
    metrics = {"impressions": 10000, "reads": 3000, "likes": 200, "favorites": 300, "comments": 50}
    _label, tier, score, _reason = viral.score_with_metrics(_f(), metrics)
    expected = 0.4 * (3000 / 10000) + 0.25 * (300 / 3000) + 0.2 * (200 / 3000) + 0.15 * (50 / 3000)
    assert tier == viral.TIER_MEASURED and score == pytest.approx(round(expected, 4))


def test_core_engagement_field_missing_is_still_incomplete_data():
    for field in ("reads", "likes", "comments"):
        metrics = {"impressions": 1000, "reads": 100, "likes": 10, "favorites": 5, "comments": 2}
        metrics[field] = None
        _label, tier, score, reason = viral.score_with_metrics(_f(), metrics)
        assert tier == viral.TIER_INCOMPLETE and score is None
        assert "缺失不等于 0" in reason


def test_no_impressions_stays_unscored():
    metrics = {"impressions": None, "reads": 8937, "likes": 437, "favorites": None, "comments": 47}
    _label, tier, score, _reason = viral.score_with_metrics(_f(), metrics)
    assert tier == viral.TIER_NO_IMPRESSIONS and score is None


def test_label_category_records_which_metric_fields_were_used(tmp_path):
    db_path = _demo_db(tmp_path, [ARTICLE_URL])
    pages = {ARTICLE_URL: html_of(ARTICLE_META, render_script(), TOOLBAR, COMMENT_BLOCK)}
    metrics_pages.collect_public_metrics(
        "demo",
        db_path=db_path,
        apply=True,
        fetch_page=_fake_fetch(pages),
        authorized=AUTHORIZED,
        sleeper=lambda _s: None,
        rules_provider=lambda _base: RobotsRules(allow_all=True),
    )
    report = viral.label_category("demo", db_path=db_path)
    assert report["tiers"].get(viral.TIER_MEASURED, 0) == 1, "拿到展现量后不再卡在 no_impressions"
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT confidence_tier, score, reason, features_json FROM viral_labels WHERE article_id=1"
        ).fetchone()
    finally:
        con.close()
    tier, score, reason, features_json = row
    assert tier == viral.TIER_MEASURED and score is not None
    features = json.loads(features_json)
    assert features["metric_fields"] == ["reads", "likes", "comments", "impressions"]
    assert "favorites" not in features["metric_fields"]
    assert "未校准" in reason


def test_public_page_samples_enable_calibration(tmp_path):
    """30 条同口径真实样本 ⇒ 阈值第一次由真实数据标定,标签不再是 unscored。"""
    urls = [f"https://www.toutiao.com/article/{7700000000000000000 + i}/" for i in range(31)]
    db_path = _demo_db(tmp_path, urls)
    con = sqlite3.connect(db_path)
    try:
        for idx in range(1, 32):
            reads = 1000 + idx * 137
            con.execute(
                "INSERT INTO article_metrics(article_id, reads, likes, favorites, comments, impressions,"
                " data_source, credibility, captured_at) VALUES(?, ?, ?, NULL, ?, ?, ?, ?, ?)",
                (
                    idx,
                    reads,
                    20 + idx * 3,
                    1 + idx,
                    5000 + idx * 900,
                    metrics_pages.SOURCE_ID,
                    "public_page",
                    "2026-11-15T17:45:00",
                ),
            )
        con.commit()
    finally:
        con.close()
    labeled = viral.label_category("demo", db_path=db_path)
    assert labeled["tiers"].get(viral.TIER_MEASURED, 0) == 31
    assert viral.calibrate("demo", db_path=db_path)["status"] == "calibrated"
    relabeled = viral.label_category("demo", db_path=db_path)
    assert relabeled["calibration"]["status"] == "calibrated"
    con = sqlite3.connect(db_path)
    try:
        labels = {row[0] for row in con.execute("SELECT DISTINCT label FROM viral_labels")}
    finally:
        con.close()
    assert labels != {viral.LABEL_UNSCORED}, "真实数据 + 标定阈值 ⇒ 标签由数据说话"


# ---------------------------------------------------------------------------
# 六、候选顺序:每轮必须往前推进,不能反复只采最新的那一批

def _seed_metrics(db_path, article_id: int, captured_at: str) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT OR REPLACE INTO article_metrics(article_id, likes, data_source, credibility, captured_at)"
            " VALUES(?, 10, ?, 'public_page', ?)",
            (article_id, metrics_pages.SOURCE_ID, captured_at),
        )
        con.commit()
    finally:
        con.close()


def _collect_ids(db_path, urls: list[str], limit: int) -> list[int]:
    report = metrics_pages.collect_public_metrics(
        "demo",
        db_path=db_path,
        limit=limit,
        apply=False,
        fetch_page=_fake_fetch({url: TOOLBAR for url in urls}),
        authorized=AUTHORIZED,
        sleeper=lambda _s: None,
        rules_provider=lambda _base: RobotsRules(allow_all=True),
    )
    return [entry["article_id"] for entry in report["pages"]]


def test_unmeasured_articles_are_picked_before_measured_ones(tmp_path):
    """已经测过的文章不许霸占每一轮的额度:未测过的先采,才能攒够标定样本。"""
    urls = [f"https://www.toutiao.com/article/{7700000000000000100 + i}/" for i in range(1, 4)]
    db_path = _demo_db(tmp_path, urls)
    _seed_metrics(db_path, 3, "2026-11-15T17:00:00")  # 最新那篇已经测过
    picked = _collect_ids(db_path, urls, limit=2)
    assert sorted(picked) == [1, 2], "候选必须轮到没测过的文章,而不是反复采同一批"


def test_stale_measurements_are_refreshed_after_fresh_ones(tmp_path):
    """都要采时先刷最久没刷的那篇(时序价值最大),再刷刚刷过的。"""
    urls = [f"https://www.toutiao.com/article/{7700000000000000200 + i}/" for i in range(1, 5)]
    db_path = _demo_db(tmp_path, urls)
    _seed_metrics(db_path, 1, "2026-11-15T09:00:00")  # 最旧
    _seed_metrics(db_path, 2, "2026-11-15T16:00:00")  # 较新
    _seed_metrics(db_path, 3, "2026-11-15T16:30:00")  # 最新
    picked = _collect_ids(db_path, urls, limit=3)
    assert picked[0] == 4, "未测过的排最前"
    assert picked[1] == 1, "已测过的按 captured_at 旧→新"
    assert picked[2] == 2


# ---------------------------------------------------------------------------
# 七、命令行:标定成功后必须立刻按新阈值重算标签

def _cli_args(**overrides):
    import argparse

    base = dict(
        list_sources=False,
        category="demo",
        snapshot=False,
        calibrate=False,
        no_relabel=False,
        fetch_public=False,
        limit=None,
        apply=False,
        sleep=None,
        import_file=None,
        source=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _seed_measured_rows(db_path, count: int) -> None:
    con = sqlite3.connect(db_path)
    try:
        for idx in range(1, count + 1):
            con.execute(
                "INSERT INTO article_metrics(article_id, reads, likes, favorites, comments, impressions,"
                " data_source, credibility, captured_at) VALUES(?, ?, ?, NULL, ?, ?, ?, ?, ?)",
                (
                    idx,
                    1000 + idx * 137,
                    20 + idx * 3,
                    1 + idx,
                    5000 + idx * 900,
                    metrics_pages.SOURCE_ID,
                    "public_page",
                    "2026-11-15T17:45:00",
                ),
            )
        con.commit()
    finally:
        con.close()


def test_calibrate_command_relabels_labels_with_new_thresholds(tmp_path, monkeypatch, capsys):
    """标定改了阈值 ⇒ 同一命令必须顺手按新阈值重算标签,不能把旧口径的标签留在库里。"""
    from ttxt import __main__ as cli
    from ttxt import config as cfg
    from ttxt import db as ttxt_db

    cat_root = tmp_path / "cats"
    monkeypatch.setattr(cfg, "CATEGORIES_DIR", cat_root)
    monkeypatch.setattr(ttxt_db, "CATEGORIES_DIR", cat_root)
    urls = [f"https://www.toutiao.com/article/{7700000000000000300 + i}/" for i in range(31)]
    db_path = store.init_articles_db("demo", db_path=cat_root / "demo" / "articles.db")
    store.insert_items(
        "demo",
        "test",
        [_item(f"标定用第 {i} 篇标题够长了吧", "正文" * 300 + f" 第 {i} 篇", url) for i, url in enumerate(urls, start=1)],
        db_path=db_path,
    )
    store.migrate_articles_db("demo", db_path=db_path)
    _seed_measured_rows(db_path, 31)

    assert cli.cmd_metrics(_cli_args(calibrate=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "calibrated"
    assert payload["relabel"]["calibration"]["status"] == "calibrated", "标定后必须重算标签,并留痕标定状态"

    con = sqlite3.connect(db_path)
    try:
        labels = {row[0] for row in con.execute("SELECT DISTINCT label FROM viral_labels")}
        reasons = {row[0] for row in con.execute("SELECT DISTINCT reason FROM viral_labels WHERE confidence_tier='measured'")}
    finally:
        con.close()
    assert labels != {viral.LABEL_UNSCORED}, "标定 + 重算之后标签必须由数据说话"
    assert reasons and all("校准阈值" in reason and "未校准" not in reason for reason in reasons), (
        f"重算后的理由必须反映已校准的阈值:{reasons}"
    )
