"""P33 阶段一(technology 真实指标补齐):逐篇结果台账 + 逐字段变更清单。

这一轮要解决的可复核问题(P25 遗留):
1. 一批候选里只有 16 条成功、4 条 404,但报告里只有 ``fetch_errors`` 这个**计数**,
   无法回答"每一条候选到底是什么结果、为什么",验收要求每条候选都有可解释状态;
2. 生产写入必须能按 article_id、指标来源、抓取时间、字段前后值追溯,
   而 ``write_metric_rows`` 只返回聚合计数,事后无法逐篇对账。

红线(全部要有断言):
- 每条候选恰好一条 outcome 记录,状态 ∈
  metrics_ok / no_public_counts / article_gone / access_blocked / fetch_failed / robots_refused;
- 文章已失效(404/410 终态)⇒ 绝不产生指标行、绝不写 0,只有台账里的可解释状态;
- 语义未取证的字段继续 NULL,并在台账里如实记 stayed_null(不是 0,也不是"没查");
- 弱来源不得覆盖强来源数值,台账里必须显示 old/new 与 protected_by_credibility;
- 证据文件(--evidence)只是报告快照,默认不写任何文件,也不改变"默认 dry-run"的口径。
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from scrapling.parser import Selector

from ttxt.analysis import metrics_import
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

OTHER_PEOPLES_CARDS = """
<ul class="related-list"><li class="related-list-item"><div class="right-content">
  <a class="title" href="/article/7701987817991488034/">别人的文章</a>
  <p class="info"><span>16万阅读</span><span>10月29日</span></p>
</div></li></ul>
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


def _metrics_row(db_path, article_id: int) -> dict:
    con = sqlite3.connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM article_metrics WHERE article_id=?", (article_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        con.close()


def _snapshot_ids(db_path) -> list[int]:
    con = sqlite3.connect(db_path)
    try:
        return [row[0] for row in con.execute("SELECT DISTINCT article_id FROM metric_snapshots ORDER BY article_id")]
    finally:
        con.close()


def _run(db_path, fetch_page, *, apply: bool = False, rules: RobotsRules | None = None) -> dict:
    return metrics_pages.collect_public_metrics(
        "demo",
        db_path=db_path,
        apply=apply,
        fetch_page=fetch_page,
        authorized=AUTHORIZED,
        sleeper=lambda _s: None,
        rules_provider=lambda _base: rules or RobotsRules(allow_all=True),
    )


# ---------------------------------------------------------------------------
# 一、每条候选都要有可解释的结果(P25 只留了计数,留不下根因)

def test_every_candidate_gets_exactly_one_outcome(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS)

    def fetch(url: str) -> Selector:
        if url == TOUTIAO_URLS[1]:
            raise FetchError("HTTP 404(终态,不重试)", status=404, terminal=True)
        if url == TOUTIAO_URLS[2]:
            raise FetchError("HTTP 403(终态,不重试)", status=403, terminal=True)
        if url == TOUTIAO_URLS[3]:
            raise RuntimeError("浏览器页面崩溃")
        return _page(TOOLBAR)

    report = _run(db_path, fetch)
    outcomes = {entry["article_id"]: entry for entry in report["outcomes"]}
    assert len(report["outcomes"]) == report["candidates"] == 4
    assert outcomes[1]["outcome"] == "metrics_ok"
    assert outcomes[2]["outcome"] == "article_gone" and outcomes[2]["status"] == 404
    assert outcomes[3]["outcome"] == "access_blocked" and outcomes[3]["status"] == 403
    assert outcomes[4]["outcome"] == "fetch_failed" and "浏览器页面崩溃" in outcomes[4]["reason"]
    assert sum(report["outcome_counts"].values()) == 4
    # 老计数字段保持原义,不因新增台账而变化
    assert report["fetched"] == 1 and report["fetch_errors"] == 3


def test_deleted_article_stays_null_with_no_row_and_no_zero(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:2])

    def fetch(url: str) -> Selector:
        if url == TOUTIAO_URLS[1]:
            raise FetchError("HTTP 404(终态,不重试)", status=404, terminal=True)
        return _page(TOOLBAR)

    report = _run(db_path, fetch, apply=True)
    assert report["written"] == 1
    assert _metrics_row(db_path, 2) == {}, "文章已失效绝不产生指标行,更不能写 0"
    assert _snapshot_ids(db_path) == [1]
    gone = next(entry for entry in report["outcomes"] if entry["article_id"] == 2)
    assert gone["outcome"] == "article_gone"
    assert gone.get("metrics_written") is False


def test_page_without_own_counts_records_no_public_counts(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:1])
    report = _run(db_path, lambda _url: _page(OTHER_PEOPLES_CARDS), apply=True)
    assert report["written"] == 0 and report["no_metrics_skipped"] == 1
    entry = report["outcomes"][0]
    assert entry["outcome"] == "no_public_counts"
    assert entry["fetched"] is True
    assert _metrics_row(db_path, 1) == {}


def test_robots_refused_candidates_are_listed_as_robots_refused(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:2])
    calls: list[str] = []

    def fetch(url: str) -> Selector:
        calls.append(url)
        return _page(TOOLBAR)

    report = _run(db_path, fetch, rules=RobotsRules(allow_all=False, unknown=True))
    assert calls == []
    assert [entry["outcome"] for entry in report["outcomes"]] == ["robots_refused", "robots_refused"]
    assert report["outcome_counts"]["robots_refused"] == 2


def test_outcome_metrics_ok_carries_observed_values_only(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:1])
    report = _run(db_path, lambda _url: _page(TOOLBAR), apply=True)
    entry = report["outcomes"][0]
    assert entry["outcome"] == "metrics_ok"
    assert entry["observed"] == {"likes": 437, "comments": 47}
    assert entry["unproven_fields"] == ["favorites"], "未取证字段必须如实记为未取证,而不是 0"
    assert entry["metrics_written"] is True
    row = _metrics_row(db_path, 1)
    assert row["favorites"] is None and row["reads"] is None and row["impressions"] is None


# ---------------------------------------------------------------------------
# 二、逐字段前后值变更清单(生产写入可追溯)

def _write(db_path, rows, source_id, captured="2026-11-16T10:00:00"):
    return metrics_import.write_metric_rows(
        "demo", rows, source_id, db_path=db_path, authorized=AUTHORIZED, captured_at=captured
    )


def test_insert_change_ledger_marks_values_and_stayed_null(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:1])
    report = _write(db_path, [{"article_id": 1, "reads": None, "likes": 100, "favorites": None, "comments": 10, "impressions": None}], metrics_pages.SOURCE_ID)
    changes = report["changes"]
    assert len(changes) == 1 and changes[0]["article_id"] == 1
    assert changes[0]["data_source"] == metrics_pages.SOURCE_ID and changes[0]["credibility"] == "public_page"
    by_field = {entry["field"]: entry for entry in changes[0]["fields"]}
    assert by_field["likes"] == {"field": "likes", "old": None, "new": 100, "action": "inserted"}
    assert by_field["comments"]["new"] == 10
    assert by_field["favorites"] == {"field": "favorites", "old": None, "new": None, "action": "stayed_null"}
    assert by_field["reads"]["action"] == "stayed_null"


def test_update_change_ledger_shows_before_and_after(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:1])
    _write(db_path, [{"article_id": 1, "likes": 100, "comments": 10, "favorites": None, "reads": None, "impressions": None}], metrics_pages.SOURCE_ID)
    report = _write(
        db_path,
        [{"article_id": 1, "likes": 150, "comments": 10, "favorites": None, "reads": 5000, "impressions": None}],
        metrics_pages.SOURCE_ID,
        captured="2026-11-17T10:00:00",
    )
    by_field = {entry["field"]: entry for entry in report["changes"][0]["fields"]}
    assert by_field["likes"] == {"field": "likes", "old": 100, "new": 150, "action": "value_changed"}
    assert by_field["comments"] == {"field": "comments", "old": 10, "new": 10, "action": "unchanged"}
    assert by_field["reads"] == {"field": "reads", "old": None, "new": 5000, "action": "filled_from_null"}
    assert by_field["favorites"] == {"field": "favorites", "old": None, "new": None, "action": "stayed_null"}
    assert report["changes"][0]["captured_at"] == "2026-11-17T10:00:00"


def test_weaker_source_protection_is_visible_in_the_ledger(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:1])
    _write(db_path, [{"article_id": 1, "likes": 999, "comments": 5, "favorites": None, "reads": None, "impressions": None}], "manual_verified")
    report = _write(db_path, [{"article_id": 1, "likes": 100, "comments": 5, "favorites": None, "reads": None, "impressions": None}], metrics_pages.SOURCE_ID)
    by_field = {entry["field"]: entry for entry in report["changes"][0]["fields"]}
    assert by_field["likes"] == {"field": "likes", "old": 999, "new": 100, "action": "protected_by_credibility"}
    row = _metrics_row(db_path, 1)
    assert row["likes"] == 999 and row["credibility"] == "verified", "弱来源不得降级强来源的数值与溯源"


def test_rejected_row_reports_error_not_silent(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:1])
    report = _write(db_path, [{"article_id": 1, "likes": None, "comments": None, "favorites": None, "reads": None, "impressions": None}], metrics_pages.SOURCE_ID)
    assert report["written"] == 0 and report["changes"] == []
    assert any("全部指标缺失" in err for err in report["row_errors"])


# ---------------------------------------------------------------------------
# 三、命令行把完整证据落成文件,stdout 只留摘要

def test_cli_evidence_file_contains_every_candidate_and_change(tmp_path, monkeypatch, capsys):
    from ttxt import __main__ as cli

    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:2])

    def fetch(url: str) -> Selector:
        if url == TOUTIAO_URLS[1]:
            raise FetchError("HTTP 404(终态,不重试)", status=404, terminal=True)
        return _page(TOOLBAR)

    report = _run(db_path, fetch, apply=True)
    evidence = tmp_path / "evidence.json"

    def fake_collect(category, db_path=None, limit=None, apply=False, **kwargs):  # noqa: A002
        return report

    monkeypatch.setattr(cli.crawler_metrics_pages, "collect_with_browser", fake_collect)
    code = cli.main([
        "metrics", "--category", "demo", "--fetch-public", "--apply", "--no-relabel",
        "--limit", "2", "--sleep", "0", "--evidence", str(evidence),
    ])
    assert code == 0
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["dry_run"] is False
    assert len(payload["outcomes"]) == 2
    assert {entry["outcome"] for entry in payload["outcomes"]} == {"metrics_ok", "article_gone"}
    assert payload["changes"][0]["fields"], "证据文件必须带逐字段前后值"
    out = capsys.readouterr().out
    assert '"written": 1' in out and '"evidence"' in out
    assert payload["evidence"].endswith("evidence.json")
    assert '"outcomes"' not in out, "stdout 只留摘要,完整逐篇证据在文件里"


def test_cli_without_evidence_writes_no_file(tmp_path, monkeypatch, capsys):
    from ttxt import __main__ as cli

    def fake_collect(category, db_path=None, limit=None, apply=False, **kwargs):  # noqa: A002
        return {"category": category, "candidates": 1, "fetched": 1, "written": 0, "dry_run": True,
                "outcomes": [{"article_id": 1, "outcome": "metrics_ok"}], "outcome_counts": {"metrics_ok": 1},
                "pages": [], "row_errors": []}

    monkeypatch.setattr(cli.crawler_metrics_pages, "collect_with_browser", fake_collect)
    assert cli.main(["metrics", "--category", "demo", "--fetch-public", "--no-relabel", "--limit", "1", "--sleep", "0"]) == 0
    out = capsys.readouterr().out
    assert "metrics_ok" in out and "dry-run" in out
    assert list(tmp_path.iterdir()) == []


def test_dry_run_evidence_is_marked_dry_run(tmp_path):
    db_path = _demo_db(tmp_path, TOUTIAO_URLS[:1])
    report = _run(db_path, lambda _url: _page(TOOLBAR), apply=False)
    assert report["dry_run"] is True and report["written"] == 0
    assert report["outcomes"][0]["metrics_written"] is False
    assert _metrics_row(db_path, 1) == {}
