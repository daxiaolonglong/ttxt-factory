"""P34 ②③:451 不再等同「文章已下架」+ 已下架文章的可追踪跳过机制。

真人验收裁定(2026-11-17)提出的两条必修项,这里逐条钉住:
- 「修正 451 与文章删除状态混用的问题,避免将法律限制误判为文章下架」
  ⇒ 451 是「因法律/政策要求不可用」,可能是临时屏蔽,不能当下架证据,更不能进跳过清单;
  404/410 才是「内容不存在」的证据。
- 「对已下架文章建立可追踪的跳过机制,避免反复请求;修改前先列影响面、备份并补测试」
  ⇒ 已确认下架的文章要落一条可追溯记录(状态/URL/时间/原因),下一轮不再发请求,
  但记录必须会过期(到期重新取证),而且只能用现成的 ``articles_meta`` kv 表 ⇒
  **不得新建表、不得给 articles 加字段**(红线由测试锁住)。

另钉两条防回归:
- dry-run 不得写任何状态(kv 也不例外);
- ``--retry-gone`` 能强制重新取证(用于事后核查,不能变成"永久拉黑")。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

import pytest
from scrapling.parser import Selector

from ttxt.crawler import metrics_pages, store
from ttxt.crawler.extract import ExtractedItem
from ttxt.crawler.fetcher import FetchError
from ttxt.crawler.robots import RobotsRules

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

TOUTIAO_URLS = [
    "https://www.toutiao.com/article/7707118928425763355/",
    "https://www.toutiao.com/article/7707526791782613519/",
    "https://www.toutiao.com/article/7706833025950073353/",
    "https://www.toutiao.com/article/7706906838314336795/",
]
AUTHORIZED = {metrics_pages.SOURCE_ID: "public_page", "manual_verified": "verified"}


def _item(title: str, content: str, url: str) -> ExtractedItem:
    return ExtractedItem(title=title, content=content, excerpt="", author=None, published_at=None, url=url)


def _demo_db(tmp_path, urls: list[str]):
    db_path = store.init_articles_db("demo", db_path=tmp_path / "demo.db")
    rows = [_item(f"第 {i} 篇测试文章标题够长了吧", "正文" * 300 + f" 第 {i} 篇", url) for i, url in enumerate(urls, start=1)]
    store.insert_items("demo", "test", rows, db_path=db_path)
    store.migrate_articles_db("demo", db_path=db_path)
    return db_path


def _page(*blocks: str) -> Selector:
    return Selector("<html><body>" + "".join(blocks) + "</body></html>")


def _run(db_path, fetch_page, *, apply: bool = False, retry_gone: bool = False, limit: int | None = None,
         rules: RobotsRules | None = None) -> dict:
    return metrics_pages.collect_public_metrics(
        "demo",
        db_path=db_path,
        limit=limit,
        apply=apply,
        retry_gone=retry_gone,
        fetch_page=fetch_page,
        authorized=AUTHORIZED,
        sleeper=lambda _s: None,
        rules_provider=lambda _base: rules or RobotsRules(allow_all=True),
    )


def _link_status(db_path) -> dict:
    con = sqlite3.connect(db_path)
    try:
        row = con.execute("SELECT value FROM articles_meta WHERE key=?", (metrics_pages.LINK_STATUS_KEY,)).fetchone()
    finally:
        con.close()
    return json.loads(row[0]) if row else {}


def _put_link_status(db_path, payload: dict) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute("INSERT OR REPLACE INTO articles_meta(key, value) VALUES(?, ?)",
                    (metrics_pages.LINK_STATUS_KEY, json.dumps(payload, ensure_ascii=False)))
        con.commit()
    finally:
        con.close()


def _metrics_rows(db_path) -> list[int]:
    con = sqlite3.connect(db_path)
    try:
        return [row[0] for row in con.execute("SELECT article_id FROM article_metrics ORDER BY article_id")]
    finally:
        con.close()


def _schema(db_path) -> tuple[list[str], list[str]]:
    """返回(表名清单, articles 列名清单),用来锁死"不加表/不加字段"这条红线。"""
    con = sqlite3.connect(db_path)
    try:
        tables = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        columns = [row[1] for row in con.execute("PRAGMA table_info(articles)")]
        return tables, columns
    finally:
        con.close()


def _gone(url: str) -> FetchError:
    return FetchError(f"HTTP 404(终态,不重试) {url}", status=404, terminal=True)


# ---------------------------------------------------------------------------
# 一、451 与「文章已删除」必须分开(真人裁定第 2 条)

def test_http_451_is_legal_block_not_article_gone(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:1])

    def fetch(url: str) -> Selector:
        raise FetchError("HTTP 451(法律要求屏蔽)", status=451, terminal=True)

    report = _run(db_path, fetch)
    entry = report["outcomes"][0]
    assert entry["outcome"] == "blocked_by_legal", "451 是法律/政策屏蔽,不能当'内容不存在'"
    assert entry["status"] == 451
    assert entry["metrics_written"] is False
    assert report["outcome_counts"]["blocked_by_legal"] == 1
    assert report["outcome_counts"].get("article_gone", 0) == 0
    assert _metrics_rows(db_path) == [], "451 同样不得伪造任何指标"


def test_http_451_never_enters_the_skip_record(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:1])

    def fetch(url: str) -> Selector:
        raise FetchError("HTTP 451(法律要求屏蔽)", status=451, terminal=True)

    report = _run(db_path, fetch, apply=True)
    assert report["link_status_recorded"] == 0
    assert _link_status(db_path) == {}, "法律屏蔽可能是临时的 ⇒ 绝不因为一次 451 就永久不再请求"


def test_404_and_410_are_article_gone_but_451_is_not(tmp_path):
    """拆开分类后,404/410 仍归 article_gone,451 单列 ⇒ 分类集合本身要钉住。"""
    assert metrics_pages._GONE_STATUSES == frozenset({404, 410})
    assert metrics_pages._LEGAL_STATUSES == frozenset({451})
    assert 451 not in metrics_pages._GONE_STATUSES

    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:3])

    def fetch(url: str) -> Selector:
        if url == TOUTIAO_URLS[0]:
            raise _gone(url)
        if url == TOUTIAO_URLS[1]:
            raise FetchError("HTTP 410(终态,不重试)", status=410, terminal=True)
        raise FetchError("HTTP 451(法律要求屏蔽)", status=451, terminal=True)

    report = _run(db_path, fetch)
    outcomes = {entry["article_id"]: entry["outcome"] for entry in report["outcomes"]}
    assert outcomes == {1: "article_gone", 2: "article_gone", 3: "blocked_by_legal"}


# ---------------------------------------------------------------------------
# 二、已下架文章的可追踪跳过机制(真人裁定第 3 条)

def test_confirmed_gone_is_recorded_with_traceable_fields(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:2])

    def fetch(url: str) -> Selector:
        if url == TOUTIAO_URLS[0]:
            raise _gone(url)
        return _page(TOOLBAR)

    report = _run(db_path, fetch, apply=True)
    status = _link_status(db_path)
    assert report["link_status_recorded"] == 1
    entry = status.get("1")
    assert entry is not None, "已确认下架的文章必须留下可追溯记录"
    assert entry["status"] == 404
    assert entry["url"] == TOUTIAO_URLS[0]
    assert entry["outcome"] == "article_gone"
    assert entry["checked_at"], "没有取证时间的记录等于无法判断是否过期"
    assert entry["reason"]


def test_recorded_gone_article_is_not_requested_next_run(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:2])

    def fetch(url: str) -> Selector:
        if url == TOUTIAO_URLS[0]:
            raise _gone(url)
        return _page(TOOLBAR)

    first = _run(db_path, fetch, apply=True)
    assert first["outcome_counts"]["article_gone"] == 1

    requested: list[str] = []

    def fetch_second(url: str) -> Selector:
        requested.append(url)
        return _page(TOOLBAR)

    second = _run(db_path, fetch_second)
    assert TOUTIAO_URLS[0] not in requested, "已确认下架的文章不该再发请求"
    assert second["known_gone_skipped"] == 1
    assert second["known_gone_ids"] == [1]
    assert all(entry["article_id"] != 1 for entry in second["outcomes"])
    # 计数守恒:台账条目数 == 本轮真正处理的候选数(被跳过的不算失败,也不算成功)
    assert len(second["outcomes"]) == second["candidates"] == 1


def test_dry_run_records_nothing(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:1])

    def fetch(url: str) -> Selector:
        raise _gone(url)

    report = _run(db_path, fetch, apply=False)
    assert report["outcome_counts"]["article_gone"] == 1
    assert report["link_status_recorded"] == 0
    assert _link_status(db_path) == {}, "dry-run 不得写任何状态,包括跳过清单"


def test_gone_record_expires_and_gets_reprobed(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:1])
    stale = (datetime.now() - timedelta(days=metrics_pages.GONE_RETRY_DAYS + 30)).isoformat(timespec="seconds")
    _put_link_status(db_path, {"1": {"status": 404, "outcome": "article_gone", "url": TOUTIAO_URLS[0],
                                     "checked_at": stale, "reason": "很久以前的取证"}})

    requested: list[str] = []

    def fetch(url: str) -> Selector:
        requested.append(url)
        return _page(TOOLBAR)

    report = _run(db_path, fetch, apply=True)
    assert requested == [TOUTIAO_URLS[0]], "跳过记录必须会过期,否则误判将永久化"
    assert report["known_gone_skipped"] == 0
    assert report["outcome_counts"]["metrics_ok"] == 1
    fresh = _link_status(db_path)
    assert fresh == {}, "重新取证成功 ⇒ 旧的失效记录要被撤销,不能既成功又留着 404 记录"


def test_retry_gone_flag_forces_a_reprobe(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:1])
    now = datetime.now().isoformat(timespec="seconds")
    _put_link_status(db_path, {"1": {"status": 404, "outcome": "article_gone", "url": TOUTIAO_URLS[0],
                                     "checked_at": now, "reason": "上一轮 404"}})

    requested: list[str] = []

    def fetch(url: str) -> Selector:
        requested.append(url)
        raise _gone(url)

    report = _run(db_path, fetch, apply=True, retry_gone=True)
    assert requested == [TOUTIAO_URLS[0]], "--retry-gone 必须能强制重新取证(跳过不是永久拉黑)"
    assert report["known_gone_skipped"] == 0
    assert report["outcome_counts"]["article_gone"] == 1


def test_skip_mechanism_adds_no_table_and_no_column(tmp_path):
    """真人裁定要求"可追踪",但 P33 红线是不加表不加字段 ⇒ 必须复用现成的 articles_meta kv。"""
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:2])
    before = _schema(db_path)

    def fetch(url: str) -> Selector:
        if url == TOUTIAO_URLS[0]:
            raise _gone(url)
        return _page(TOOLBAR)

    _run(db_path, fetch, apply=True)
    assert _schema(db_path) == before, "跳过机制只能落在现成的 articles_meta kv 里,不得改结构"
    assert "articles_meta" in before[0]


def test_skip_record_is_per_run_dated_and_survives_mixed_outcomes(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:4])

    def fetch(url: str) -> Selector:
        if url == TOUTIAO_URLS[0]:
            raise _gone(url)
        if url == TOUTIAO_URLS[1]:
            raise FetchError("HTTP 451(法律要求屏蔽)", status=451, terminal=True)
        if url == TOUTIAO_URLS[2]:
            raise RuntimeError("浏览器页面崩溃")
        return _page(TOOLBAR)

    report = _run(db_path, fetch, apply=True)
    status = _link_status(db_path)
    assert sorted(status) == ["1"], "只有真正确认内容不存在(404/410)的才进跳过清单"
    assert report["outcome_counts"] == {"article_gone": 1, "blocked_by_legal": 1, "fetch_failed": 1, "metrics_ok": 1}


def test_skip_counters_are_library_wide_even_with_a_small_limit(tmp_path):
    """limit 只限制"本轮挑几篇",不得让跳过计数失真(实测过:旧写法 limit=2 时报 0)。"""
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:2])
    extra = ExtractedItem(title="外部来源的文章标题也够长", content="正文" * 300, excerpt="", author=None,
                          published_at=None, url="https://www.ifanr.com/archives/1447")
    store.insert_items("demo", "test", [extra], db_path=db_path)

    first = _run(db_path, lambda url: (_ for _ in ()).throw(_gone(url)), apply=True)
    assert first["outcome_counts"] == {"article_gone": 2}

    def boom(url: str) -> Selector:
        raise AssertionError(f"已确认下架的文章又被请求了:{url}")

    second = _run(db_path, boom, limit=1)
    assert second["candidates"] == 0
    assert second["known_gone_skipped"] == 2 and second["known_gone_ids"] == [2, 1]
    assert second["host_skipped"] == 1, "白名单外的文章也要按全库口径统计,不会因为挑满就少报"


def test_limit_zero_makes_no_requests(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:1])

    def boom(url: str) -> Selector:
        raise AssertionError(f"limit=0 却发了请求:{url}")

    report = _run(db_path, boom, limit=0)
    assert report["candidates"] == 0 and report["fetched"] == 0


# ---------------------------------------------------------------------------
# 三、命令行摘要要看得见"跳过了几篇"


def test_cli_summary_reports_known_gone(tmp_path, monkeypatch, capsys):
    from ttxt import __main__ as cli

    def fake_collect(category, db_path=None, limit=None, apply=False, retry_gone=False, **kwargs):  # noqa: A002
        assert retry_gone is False, "默认不得绕过跳过机制"
        return {"category": category, "candidates": 1, "fetched": 1, "written": 1, "dry_run": False,
                "outcomes": [{"article_id": 2, "outcome": "metrics_ok"}], "outcome_counts": {"metrics_ok": 1},
                "known_gone_skipped": 4, "known_gone_ids": [70, 149, 156, 233],
                "link_status_recorded": 0, "pages": [], "row_errors": []}

    monkeypatch.setattr(cli.crawler_metrics_pages, "collect_with_browser", fake_collect)
    assert cli.main(["metrics", "--category", "demo", "--fetch-public", "--apply", "--no-relabel",
                     "--limit", "1", "--sleep", "0"]) == 0
    out = capsys.readouterr().out
    assert '"known_gone_skipped": 4' in out, "摘要必须显示本轮跳过了几篇已确认下架的文章"
    assert '"known_gone_ids"' in out and "233" in out


def test_cli_retry_gone_flag_reaches_the_collector(tmp_path, monkeypatch, capsys):
    from ttxt import __main__ as cli

    seen: list[bool] = []

    def fake_collect(category, db_path=None, limit=None, apply=False, retry_gone=False, **kwargs):  # noqa: A002
        seen.append(retry_gone)
        return {"category": category, "candidates": 0, "fetched": 0, "written": 0, "dry_run": True,
                "outcomes": [], "outcome_counts": {}, "known_gone_skipped": 1, "known_gone_ids": [70],
                "link_status_recorded": 0, "pages": [], "row_errors": []}

    monkeypatch.setattr(cli.crawler_metrics_pages, "collect_with_browser", fake_collect)
    assert cli.main(["metrics", "--category", "demo", "--fetch-public", "--retry-gone", "--no-relabel",
                     "--limit", "1", "--sleep", "0"]) == 0
    assert seen == [True], "--retry-gone 必须传到采集器"


# ---------------------------------------------------------------------------
# 四、P35 真人复核提出的两个时间边界
#   ① 未来时间的台账记录(系统时钟错乱)不能算"有效期内",否则反而会把好文章永久跳过;
#   ② checked_at 必须是**这篇文章自己的取证时刻**,不能整批共用批次开始时间 ——
#      否则 90 天有效期会被批次开始时间悄悄推后/提前,对账时也说不清"哪一刻看到的 404"。


def test_status_fresh_rejects_future_stamps_but_tolerates_small_skew():
    now = datetime.now().astimezone()

    def entry(days: float) -> dict:
        return {"status": 404, "outcome": metrics_pages.OUTCOME_GONE, "url": "https://x/",
                "checked_at": (now + timedelta(days=days)).isoformat(timespec="seconds")}

    assert metrics_pages._status_fresh(entry(-30), now) is True, "30 天内的记录仍然有效"
    assert metrics_pages._status_fresh(entry(-(metrics_pages.GONE_RETRY_DAYS + 1)), now) is False, "过期要重新取证"
    assert metrics_pages._status_fresh(entry(7), now) is False, "未来 7 天 = 时钟错乱,记录不可信"
    assert metrics_pages._status_fresh(entry(3 / 1440), now) is True, "几分钟的时差是正常抖动,不该重新请求"
    assert metrics_pages._status_fresh({"status": 451, "checked_at": now.isoformat()}, now) is False


def test_future_checked_at_does_not_keep_an_article_skipped(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:1])
    future = (datetime.now().astimezone() + timedelta(days=7)).isoformat(timespec="seconds")
    _put_link_status(db_path, {"1": {"status": 404, "outcome": metrics_pages.OUTCOME_GONE,
                                     "url": TOUTIAO_URLS[0], "checked_at": future, "reason": "时钟错乱"}})
    called: list[str] = []

    def fetch(url: str) -> Selector:
        called.append(url)
        return _page(TOOLBAR)

    report = _run(db_path, fetch, limit=1)
    assert called == [TOUTIAO_URLS[0]], "台账里的未来时间不可信,必须重新取证"
    assert report["known_gone_skipped"] == 0, "坏记录不能计入'已跳过',否则掩盖了真实请求数"


def test_gone_record_stamps_each_article_its_own_evidence_time(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:2])
    ticks = iter(["2026-11-17T09:00:00+08:00", "2026-11-17T09:01:30+08:00"])

    def fetch(url: str) -> Selector:
        raise _gone(url)

    report = metrics_pages.collect_public_metrics(
        "demo", db_path=db_path, apply=True,
        fetch_page=fetch, authorized=AUTHORIZED, sleeper=lambda _s: None,
        rules_provider=lambda _b: RobotsRules(allow_all=True),
        captured_at="2026-11-17T09:00:00+08:00",   # 批次时刻(整批共用)
        clock=lambda: next(ticks),                 # 每篇文章各自的取证时刻
    )
    assert [o["outcome"] for o in report["outcomes"]] == ["article_gone", "article_gone"]
    stamps = {o["checked_at"] for o in report["outcomes"]}
    assert stamps == {"2026-11-17T09:00:00+08:00", "2026-11-17T09:01:30+08:00"}, \
        "每条结果要带自己的取证时刻,不能整批共用批次开始时间"
    ledger = _link_status(db_path)
    assert {ledger["1"]["checked_at"], ledger["2"]["checked_at"]} == stamps, \
        "台账里的 checked_at 必须是这篇文章自己的时刻"


def test_round_plan_is_read_only_and_matches_the_round(tmp_path):
    """调度器要先判断"这轮有没有活可干",不值得为空轮次开浏览器 ⇒ 提供只读的 round_plan。"""
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:3])
    plan = metrics_pages.round_plan("demo", db_path=db_path, limit=2)
    assert plan["candidates"] == 2 and plan["picked_ids"] == [3, 2]
    assert plan["host_skipped"] == 0 and plan["known_gone_skipped"] == 0

    fresh = {"status": 404, "outcome": metrics_pages.OUTCOME_GONE, "url": TOUTIAO_URLS[2],
             "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"), "reason": "404"}
    _put_link_status(db_path, {"3": fresh})
    again = metrics_pages.round_plan("demo", db_path=db_path, limit=2)
    assert again["picked_ids"] == [2, 1], "已确认下架的文章在计划里就该被排除"
    assert again["known_gone_skipped"] == 1 and again["known_gone_ids"] == [3]
    assert again["candidates"] == 2

    assert list(_link_status(db_path)) == ["3"], "只读计划不得改写台账"
    assert _metrics_rows(db_path) == [], "只读计划不得写指标"
    tables, columns = _schema(db_path)
    assert "articles_meta" in tables and "checked_at" not in columns
