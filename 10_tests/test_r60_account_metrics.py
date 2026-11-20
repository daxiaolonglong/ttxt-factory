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
