"""R60 G1:自有账号后台指标回流(收藏)的纯函数与接线测试。

不启动浏览器。钉住四件事:
1. 后台 JSON 解析只认已知字段,缺失保持缺失(不填 0);
2. 只有 URL/唯一标题/item_id 三种高置信匹配才写库,同名多篇宁缺毋滥;
3. 写入必须走 ``metrics_import.write_metric_rows``,来源 ``toutiao_account`` 已进白名单;
4. 调度器把未登录/节流/占用如实记成 skipped_*,不伪装成功。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from ttxt import config as cfg
from ttxt import scheduler
from ttxt.analysis import metrics_import
from ttxt.crawler import account_metrics, store
from ttxt.crawler.extract import ExtractedItem


def _item(title: str, url: str) -> ExtractedItem:
    # 内容带上 URL,避免同标题两篇被 simhash 去重(本测试要保留同名多篇)
    return ExtractedItem(title=title, content="正文" * 300 + f" {url}", excerpt="", author=None,
                         published_at=None, url=url)


def _demo_db(tmp_path):
    db_path = store.init_articles_db("demo", db_path=tmp_path / "demo.db")
    rows = [
        _item("唯一标题文章甲", "https://www.toutiao.com/article/7001/"),
        _item("重复标题文章乙", "https://www.toutiao.com/article/7002/"),
        _item("重复标题文章乙", "https://www.toutiao.com/article/7003/"),
    ]
    store.insert_items("demo", "test", rows, db_path=db_path)
    store.migrate_articles_db("demo", db_path=db_path)
    con = sqlite3.connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        return db_path, {row["url"]: int(row["id"]) for row in
                         con.execute("SELECT id,url FROM articles")}
    finally:
        con.close()


def _metric_row(db_path, article_id: int) -> dict:
    con = sqlite3.connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM article_metrics WHERE article_id=?", (article_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        con.close()


# --------------------------------------------------------------------------- 一、解析

def test_extract_metric_items_known_fields_only_and_no_zero_fill():
    payload = {"data": {"list": [
        {"title": "有收藏的文章", "item_id": 7001, "read_count": "1,234",
         "favorite_count": 17, "digg_count": 9, "comment_count": 3},
        {"title": "只有阅读", "read_count": 5},
        {"title": "一个计数都没有", "author": "x"},
        {"no_title": True, "read_count": 1},
    ]}}
    items = account_metrics.extract_metric_items(payload)
    assert len(items) == 2
    first = items[0]
    assert first["reads"] == 1234 and first["favorites"] == 17
    assert first["likes"] == 9 and first["comments"] == 3
    second = items[1]
    assert second["reads"] == 5
    assert second["favorites"] is None and second["likes"] is None and second["comments"] is None, \
        "缺失字段必须保持 None,不能填 0"


def test_extract_handles_nested_mp_provider_shape():
    # 实测后台 mp_provider feed 的嵌套结构:计数在 itemCounter、标题/链接在 articleBase
    payload = {"data": [{"assembleCell": {"itemCell": {
        "itemCounter": {"commentCount": 0, "diggCount": 5, "readCount": 30,
                        "repinCount": 1, "showCount": 70},
        "articleBase": {"gidStr": "1878743119879176", "title": "我的作品",
                        "displayURL": "https://www.toutiao.com/a1878743119879176"},
    }}}]}
    items = account_metrics.extract_metric_items(payload)
    assert len(items) == 1
    item = items[0]
    assert item["title"] == "我的作品"
    assert item["reads"] == 30 and item["likes"] == 5 and item["comments"] == 0
    assert item["impressions"] == 70
    assert item["item_id"] == "1878743119879176"
    assert item["favorites"] is None, "该接口没有收藏字段,必须保持 None"


def test_interval_blocked_uses_last_success_only():
    now = datetime(2026, 11, 21, 2, 0, tzinfo=timezone.utc)
    assert account_metrics.interval_blocked({}, now) is None
    assert account_metrics.interval_blocked(
        {"last_fetch_ok_at": (now - timedelta(hours=1)).isoformat()}, now) == 3600.0
    assert account_metrics.interval_blocked(
        {"last_fetch_ok_at": (now - timedelta(hours=3)).isoformat()}, now) is None
    assert account_metrics.interval_blocked({"last_fetch_ok_at": "not-a-date"}, now) is None


# --------------------------------------------------------------------------- 二、匹配与写入

def test_match_and_write_url_item_id_and_unique_title(tmp_path):
    db_path, ids = _demo_db(tmp_path)
    items = [
        # URL 命中
        {"title": "标题可以不同", "url": "https://www.toutiao.com/article/7001/",
         "reads": 100, "favorites": 7, "likes": 5, "comments": 2},
        # item_id 命中(存储 URL 是 www 形态)
        {"title": "随便", "item_id": "7002", "reads": 50, "favorites": 3},
        # 同名多篇 -> 不能猜
        {"title": "重复标题文章乙", "reads": 999, "favorites": 9},
        # 完全不认识
        {"title": "库里没有的标题", "reads": 1, "favorites": 1},
    ]
    report = account_metrics.match_and_write("demo", items, db_path=db_path, apply=True)
    assert report["matched"] == 2
    assert report["unmatched"] == 2
    assert report["write"]["written"] >= 2

    first = _metric_row(db_path, ids["https://www.toutiao.com/article/7001/"])
    assert first["reads"] == 100 and first["favorites"] == 7
    assert first["likes"] == 5 and first["comments"] == 2
    assert first["data_source"] == account_metrics.SOURCE_ID
    assert first["credibility"] == "self_reported"

    second = _metric_row(db_path, ids["https://www.toutiao.com/article/7002/"])
    assert second["reads"] == 50 and second["favorites"] == 3
    # 重复标题的两篇都不许被写
    for url in ("https://www.toutiao.com/article/7002/", "https://www.toutiao.com/article/7003/"):
        row = _metric_row(db_path, ids[url])
        assert row.get("favorites") in (None, 3 if url.endswith("7002/") else None)


def test_match_and_write_dry_run_writes_nothing(tmp_path):
    db_path, ids = _demo_db(tmp_path)
    report = account_metrics.match_and_write(
        "demo", [{"url": "https://www.toutiao.com/article/7001/", "favorites": 7}],
        db_path=db_path, apply=False)
    assert report["matched"] == 1 and report["applied"] is False
    assert _metric_row(db_path, ids["https://www.toutiao.com/article/7001/"]) == {}


# --------------------------------------------------------------------------- 三、白名单与调度接线

def test_account_source_is_authorized():
    sources = metrics_import.load_authorized_sources()
    assert sources.get(account_metrics.SOURCE_ID) == "self_reported"


def test_scheduler_has_disabled_account_job_until_login():
    jobs = scheduler.load_jobs()["jobs"]
    job = next(item for item in jobs if item.get("kind") == "account_metrics")
    assert job["interval_min"] == 120
    assert job["enabled"] is False, "未扫码登录前不得自动开浏览器"
    assert job["apply"] is True


def test_scheduler_maps_account_status_without_faking_ok(monkeypatch):
    calls = []

    def fake_fetch(*, apply, categories):
        calls.append((apply, categories))
        return {"status": "not_logged_in", "note": "先扫码"}

    monkeypatch.setattr(account_metrics, "account_fetch_metrics", fake_fetch)
    result = scheduler.run_job({"id": "account", "kind": "account_metrics", "apply": True})
    assert result["scheduler_status"] == "skipped_not_logged_in"
    assert calls == [(True, None)]

    monkeypatch.setattr(account_metrics, "account_fetch_metrics",
                        lambda **_: {"status": "ok", "items": 3, "categories": [
                            {"category": "demo", "matched": 2}]})
    result = scheduler.run_job({"id": "account", "kind": "account_metrics", "apply": True,
                                "category": "demo"})
    assert result["scheduler_status"] == "ok"
    assert result["total_matched"] == 2


REAL_MODAL_TEXT = """张顺复盘AI视频创业：200万元烧完后，转向开源工具与精简团队
微头条
10-11 16:57
部分数据更新有延迟
流量
展现量73
粉丝展现量0
阅读量0
粉丝阅读量0
点击率0%
平均阅读时长--
收益
创作收益0 元
粉丝
粉丝变化0
涨粉0掉粉0
阅读转粉率0% 
互动
评论0
点赞0
转发0
分享0
收藏1
~
消费分析收益分析用户画像
"""

MODAL_WITH_ACTIONS = """AI改简历做副业，先看清门槛和风险
09-13 17:58
流量
展现量21
阅读量0
查看评论
修改
更多
消费分析
"""

LEGACY_MODAL = """老版样式作品
展现量500
阅读量60
评论3
点赞8
收藏12
"""


def test_parse_stat_modal_reads_favorites_from_interaction_block():
    item = account_metrics.parse_stat_modal(REAL_MODAL_TEXT)
    assert item is not None
    assert item["title"].startswith("张顺复盘AI视频创业")
    assert item["favorites"] == 1, "收藏是本轮要打通的关键字段"
    assert item["comments"] == 0 and item["likes"] == 0
    assert item["impressions"] == 73 and item["reads"] == 0


def test_parse_stat_modal_refuses_interaction_guessing_without_block():
    item = account_metrics.parse_stat_modal(MODAL_WITH_ACTIONS)
    assert item is not None
    assert item["impressions"] == 21 and item["reads"] == 0
    # 有"查看评论"操作链接但没有"互动"分区 ⇒ 互动数字宁缺毋滥
    assert item["favorites"] is None and item["comments"] is None and item["likes"] is None


def test_parse_stat_modal_legacy_fulltext_shape_still_parsed():
    item = account_metrics.parse_stat_modal(LEGACY_MODAL)
    assert item is not None
    assert (item["impressions"], item["reads"], item["comments"], item["likes"],
            item["favorites"]) == (500, 60, 3, 8, 12)


def test_merge_modal_items_backfills_favorites_into_feed_item():
    feed = [{"title": "甲", "url": "https://www.toutiao.com/article/1/", "item_id": "1",
             "reads": 14, "favorites": None, "likes": 0, "comments": 0, "impressions": 688}]
    modal = [{"title": "甲", "url": None, "item_id": None,
              "reads": 14, "favorites": 1, "likes": 0, "comments": 0, "impressions": 688},
             {"title": "乙", "url": None, "item_id": None,
              "reads": None, "favorites": 5, "likes": None, "comments": None,
              "impressions": None}]
    merged = account_metrics.merge_modal_items(feed, modal)
    assert merged[0]["favorites"] == 1
    assert merged[0]["reads"] == 14 and merged[0]["url"]
    assert len(merged) == 2 and merged[1]["title"] == "乙"


def test_register_own_work_is_idempotent_and_metrics_attach(tmp_path, monkeypatch):
    db_path = store.init_articles_db("demo", db_path=tmp_path / "demo.db")
    monkeypatch.setattr(account_metrics.cfg, "category_db_path",
                        lambda category: db_path)
    item = {"title": "我方已发布作品", "url": "https://www.toutiao.com/item/7000000000000000001/",
            "item_id": "7000000000000000001", "reads": 3, "favorites": 2,
            "likes": 1, "comments": 0, "impressions": 995}
    # 匹配写库默认不再登记(防跨类目重复登记):未命中就只落 unmatched
    solo = account_metrics.match_and_write("demo", [dict(item)], db_path=db_path, apply=True)
    assert solo["matched"] == 0 and solo["registered"] == 0
    # 第一轮:register_pass 全库判重后登记一次 → match_and_write url 命中挂指标
    reg = account_metrics.register_pass([dict(item)], ["demo"], own_work_category="demo")
    assert len(reg.get("demo", [])) == 1, "首轮应登记 1 条"
    first = account_metrics.match_and_write("demo", [dict(item)], db_path=db_path, apply=True)
    assert first["matched"] == 1
    # 第二轮:已登记 → 判重命中,不再登记;url 直接命中
    again = account_metrics.register_pass([dict(item)], ["demo"], own_work_category="demo")
    assert again == {}, "同一作品重复取数不得重复登记"
    second = account_metrics.match_and_write("demo", [dict(item)], db_path=db_path, apply=True)
    assert second["matched"] == 1
    con = account_metrics.connect(db_path)
    rows = con.execute(
        "SELECT m.favorites, m.reads, m.data_source, a.source FROM article_metrics m"
        " JOIN articles a ON a.id=m.article_id").fetchall()
    own_count = con.execute("select count(*) from articles where source='own_account'").fetchone()[0]
    con.close()
    assert own_count == 1, "同一作品多轮取数全库只允许一条登记"
    assert rows and rows[0]["favorites"] == 2, "自有作品收藏必须能落库(验收口径 favorites 非空 >0)"
    assert rows[0]["data_source"] == account_metrics.SOURCE_ID
    assert rows[0]["source"] == account_metrics.OWN_SOURCE


def test_register_pass_skips_categories_that_already_have_the_work(tmp_path, monkeypatch):
    """库内已有同 url 文章(哪怕在别的类目)→ 不再登记,避免跨类目重复。"""
    other = store.init_articles_db("other", db_path=tmp_path / "other.db")
    target = store.init_articles_db("demo", db_path=tmp_path / "demo.db")
    con = account_metrics.connect(other)
    con.execute(
        "INSERT INTO articles(category, source, title, content, url, url_normalized,"
        " fetched_at, content_hash, completeness, credibility, processor_version)"
        " VALUES('other','x','已存在作品','正文','https://www.toutiao.com/item/99/"
        "','https://www.toutiao.com/item/99/','2026-01-01T00:00:00','h','full','x','v')")
    con.commit()
    con.close()
    paths = {"other": other, "demo": target}
    monkeypatch.setattr(account_metrics.cfg, "category_db_path", lambda c: paths[c])
    item = {"title": "已存在作品", "url": "https://www.toutiao.com/item/99/", "favorites": 1}
    reg = account_metrics.register_pass([item], ["other", "demo"], own_work_category="demo")
    assert reg == {}
    con = account_metrics.connect(target)
    assert con.execute("select count(*) from articles").fetchone()[0] == 0
    con.close()


def test_register_pass_title_hit_in_target_category_blocks_re_registration(tmp_path, monkeypatch):
    """库内已有同名文章(url 形态不同)在**目标类目自己身上** → 不得再登记(实测污染根因)。"""
    target = store.init_articles_db("demo", db_path=tmp_path / "demo.db")
    con = account_metrics.connect(target)
    con.execute(
        "INSERT INTO articles(category, source, title, content, url, url_normalized,"
        " fetched_at, content_hash, completeness, credibility, processor_version)"
        " VALUES('demo','legacy_factory_search_ssr','同名作品','正文采集',"
        "'https://www.toutiao.com/article/177/','https://www.toutiao.com/article/177/',"
        "'2026-01-01T00:00:00','h2','full','x','v')")
    con.commit()
    con.close()
    monkeypatch.setattr(account_metrics.cfg, "category_db_path", lambda c: target)
    item = {"title": "同名作品", "url": "https://www.toutiao.com/a187874/",
            "item_id": "187874", "favorites": 1}
    reg = account_metrics.register_pass([item], ["demo"], own_work_category="demo")
    assert reg == {}, "目标类目库里同名即视为已有,登记会造重复行"
    con = account_metrics.connect(target)
    assert con.execute("select count(*) from articles").fetchone()[0] == 1
    con.close()


def test_dedup_captured_items_unions_fields_across_responses():
    items = [
        {"title": "甲", "url": "https://www.toutiao.com/a1/?mod=1", "item_id": "1",
         "reads": 14, "favorites": None, "likes": 0, "comments": 0, "impressions": 693},
        {"title": "", "url": "https://www.toutiao.com/a1/", "item_id": None,
         "reads": None, "favorites": 1, "likes": None, "comments": None, "impressions": None},
        {"title": "甲", "url": "", "item_id": None,
         "reads": None, "favorites": 1, "likes": 0, "comments": 0, "impressions": None},
        {"title": "乙", "url": "https://www.toutiao.com/a2/", "reads": 3},
    ]
    out = account_metrics.dedup_captured_items(items)
    assert len(out) == 3, "url 同路径跨响应合并;无 url 的弹层条目不得仅凭标题并库(宁缺毋滥)"
    assert out[0]["favorites"] == 1 and out[0]["reads"] == 14 and out[0]["item_id"] == "1"
    assert out[1]["title"] == "甲" and not out[1]["url"]
    assert out[2]["title"] == "乙"
