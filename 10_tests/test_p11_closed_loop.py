"""P11 闭环测试:授权指标入库、时序快照、预测沉淀、关键词反哺、本地调度与面板任务下发护栏。

诚实性红线(必须全部有断言覆盖):
- 未授权来源指标一律拒收;缺失不伪造为 0;跨类目行拒写;负数拒收;
- 每次导入都追加 metric_snapshots(时序不可覆盖历史);
- 没有 measured 样本绝不预测;settle 只回填 measured;
- 预测准确率知识资产必须达到最少样本,重复报告只保留一条;
- 关键词零覆盖 → gap → 只对正式启用来源入队(演示类目不入队),重复不叠加,gap 恢复回 active;
- 调度器禁止 write/publish/model 类任务;禁用即整体跳过;单任务失败隔离;
- 面板 POST 缺 X-TTXT-Task / 非回环 Host / Origin 不匹配全部拒绝;非 /api/tasks/* 一律 405。
"""

import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from ttxt import db, scheduler
from ttxt.analysis import keywords, metrics_import, prediction, viral
from ttxt.crawler import sources as source_loader
from ttxt.crawler import store
from ttxt.crawler.extract import ExtractedItem
from ttxt.dashboard import api, server


# ---------------------------------------------------------------------------
# 公共夹具


def _item(title: str, content: str, url: str) -> ExtractedItem:
    return ExtractedItem(
        title=title, content=content, excerpt="", author="", published_at=None, url=url
    )


def _demo_db(tmp_path, articles: int = 1):
    db_path = store.init_articles_db("demo", db_path=tmp_path / "demo.db")
    rows = [
        _item(
            f"人工智能会改变什么吗?{i}",
            f"人工智能与开源效率工具深度结合,数据分析变得前所未有地简单。第 {i} 篇。",
            f"https://example.com/demo/{i}",
        )
        for i in range(1, articles + 1)
    ]
    store.insert_items("demo", "test", rows, db_path=db_path)
    store.migrate_articles_db("demo", db_path=db_path)
    return db_path


def _write(path, text: str):
    path.write_text(text, encoding="utf-8")
    return path


def _write_sources(tmp_path, entries: list[dict]):
    path = tmp_path / "sources.json"
    _write(path, json.dumps({"sources": entries}, ensure_ascii=False))
    return path


def _crawl_jobs(system_db):
    con = sqlite3.connect(system_db)
    try:
        return [dict(zip(("id", "source", "category", "status", "meta_json"), row))
                for row in con.execute("SELECT id, source, category, status, meta_json FROM crawl_jobs")]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 指标入库:授权与诚实性


def test_import_rejects_unauthorized_source(tmp_path):
    db_path = _demo_db(tmp_path)
    csv_path = _write(tmp_path / "m.csv", "article_id,reads\n1,100\n")
    with pytest.raises(PermissionError):
        metrics_import.import_metrics("demo", csv_path, "random_blog", db_path=db_path,
                                      authorized={"unit_test_source": "verified"})


def test_import_missing_null_negative_and_cross_category(tmp_path):
    db_path = _demo_db(tmp_path)
    csv_path = _write(
        tmp_path / "m.csv",
        "article_id,reads,likes,favorites,comments,impressions\n"
        "1,,20,5,2,1000\n"      # 缺 reads:允许 NULL,不伪造
        "1,-5,20,5,2,1000\n"    # 负数:该行拒绝
        "999,100,20,5,2,1000\n" # 不存在的文章:拒绝
        "1,,,,,\n",             # 全缺失:拒绝写入
    )
    result = metrics_import.import_metrics("demo", csv_path, "unit_test_source", db_path=db_path,
                                           authorized={"unit_test_source": "verified"})
    assert result["written"] == 1
    assert len(result["row_errors"]) == 3
    con = sqlite3.connect(db_path)
    try:
        reads, credibility = con.execute(
            "SELECT reads, credibility FROM article_metrics WHERE article_id=1"
        ).fetchone()
        assert reads is None          # 缺失 = NULL,不是 0
        assert credibility == "verified"
    finally:
        con.close()

    # 跨类目:文章属于 demo,导入 tech 类目必须整行拒写
    csv2 = _write(tmp_path / "m2.csv", "article_id,reads\n1,100\n")
    result2 = metrics_import.import_metrics("tech", csv2, "unit_test_source", db_path=db_path,
                                            authorized={"unit_test_source": "verified"})
    assert result2["written"] == 0 and result2["updated"] == 0
    assert any("跨类目" in err for err in result2["row_errors"])


def test_import_empty_file_and_url_match(tmp_path):
    db_path = _demo_db(tmp_path)
    empty = _write(tmp_path / "e.csv", "")
    result = metrics_import.import_metrics("demo", empty, "unit_test_source", db_path=db_path,
                                           authorized={"unit_test_source": "verified"})
    assert result["written"] == 0 and result["row_errors"]

    by_url = _write(
        tmp_path / "u.csv",
        "url,reads,likes,favorites,comments,impressions\n"
        "https://example.com/demo/1,500,50,20,5,2000\n",
    )
    result2 = metrics_import.import_metrics("demo", by_url, "unit_test_source", db_path=db_path,
                                            authorized={"unit_test_source": "verified"})
    assert result2["written"] == 1 and result2["snapshots"] == 1


def test_two_imports_two_snapshots_and_relabel(tmp_path):
    db_path = _demo_db(tmp_path)
    first = _write(tmp_path / "a.csv",
                   "article_id,reads,likes,favorites,comments,impressions\n1,100,10,5,1,1000\n")
    second = _write(tmp_path / "b.csv",
                    "article_id,reads,likes,favorites,comments,impressions\n1,150,12,6,2,1200\n")
    authorized = {"unit_test_source": "verified"}
    r1 = metrics_import.import_metrics("demo", first, "unit_test_source", db_path=db_path, authorized=authorized)
    r2 = metrics_import.import_metrics("demo", second, "unit_test_source", db_path=db_path, authorized=authorized)
    assert (r1["written"], r1["snapshots"]) == (1, 1)
    assert (r2["updated"], r2["snapshots"]) == (1, 1)   # upsert 覆盖现值,快照追加
    con = sqlite3.connect(db_path)
    try:
        assert con.execute("SELECT COUNT(*) FROM metric_snapshots").fetchone()[0] == 2
        assert con.execute("SELECT reads FROM article_metrics WHERE article_id=1").fetchone()[0] == 150
        snaps = [row[0] for row in con.execute(
            "SELECT reads FROM metric_snapshots ORDER BY id")]
        assert snaps == [100, 150]                     # 历史不被覆盖
    finally:
        con.close()

    # 再冻结一份当前快照
    snap = metrics_import.snapshot_current_metrics("demo", db_path=db_path, captured_at="2026-11-12T20:00:00")
    assert snap["snapshots"] == 1

    # 导入后自动重标:reads/likes/favorites/comments/impressions 齐全 → measured
    relabel = metrics_import.relabel_after_import("demo", db_path=db_path)
    assert relabel["tiers"].get("measured") == 1

    # 不完整数据(缺 impressions)→ 绝不 measured
    partial = _write(tmp_path / "c.csv", "article_id,reads\n1,999\n")
    metrics_import.import_metrics("demo", partial, "unit_test_source", db_path=db_path, authorized=authorized)
    con = sqlite3.connect(db_path)
    try:
        con.execute("UPDATE article_metrics SET impressions=NULL WHERE article_id=1")
        con.commit()
    finally:
        con.close()
    relabel2 = metrics_import.relabel_after_import("demo", db_path=db_path)
    assert "measured" not in relabel2["tiers"]


def test_import_rejects_unknown_extension(tmp_path):
    db_path = _demo_db(tmp_path)
    bad = _write(tmp_path / "m.txt", "1,100\n")
    with pytest.raises(ValueError):
        metrics_import.import_metrics("demo", bad, "unit_test_source", db_path=db_path,
                                      authorized={"unit_test_source": "verified"})


# ---------------------------------------------------------------------------
# 预测闭环


def _measured_db(tmp_path):
    """3 篇实测 measured + 2 篇无标签文章(供 predict)。"""
    db_path = store.init_articles_db("demo", db_path=tmp_path / "demo.db")
    labeled = [
        _item(f"人工智能会取代谁?{i}", "人工智能内容一二三。" * 20, f"https://example.com/p/{i}")
        for i in range(1, 4)
    ]
    store.insert_items("demo", "test", labeled, db_path=db_path)
    store.migrate_articles_db("demo", db_path=db_path)
    csv_path = _write(
        tmp_path / "measured.csv",
        "article_id,reads,likes,favorites,comments,impressions\n"
        "1,5000,600,400,80,10000\n2,3000,300,200,40,9000\n3,1500,100,60,10,8000\n",
    )
    metrics_import.import_metrics("demo", csv_path, "unit_test_source", db_path=db_path,
                                  authorized={"unit_test_source": "verified"})
    viral.label_category("demo", db_path=db_path)
    extra = [
        _item("开源模型哪个更强?4", "开源模型评测内容。" * 20, "https://example.com/p/4"),
        _item("人工智能入门指南5", "人工智能教程内容。" * 20, "https://example.com/p/5"),
    ]
    store.insert_items("demo", "test", extra, db_path=db_path)
    return db_path


def test_predict_refuses_without_measured(tmp_path):
    db_path = _demo_db(tmp_path)
    result = prediction.predict_articles("demo", db_path=db_path)
    assert result["recorded"] == 0
    assert "measured" in result["reason"]


def test_predict_settle_and_accuracy_asset(tmp_path):
    db_path = _measured_db(tmp_path)
    first = prediction.predict_articles("demo", db_path=db_path)
    assert first["recorded"] == 2
    basis = {json.loads(row[0])["bucket"]
             for row in _rows(db_path, "SELECT basis FROM prediction_records")}
    assert basis == {"question", "number"}

    # 幂等:已有预测记录不重复登记
    again = prediction.predict_articles("demo", db_path=db_path)
    assert again["recorded"] == 0 and again["already_has_prediction"] == 2

    # 未 measured 前 settle 不回填
    pre = prediction.settle_predictions("demo", db_path=db_path)
    assert pre["settled"] == 0 and pre["pending"] == 2

    csv2 = _write(tmp_path / "measured2.csv",
                  "article_id,reads,likes,favorites,comments,impressions\n"
                  "4,2000,200,150,30,5000\n5,800,40,20,4,4000\n")
    metrics_import.import_metrics("demo", csv2, "unit_test_source", db_path=db_path,
                                  authorized={"unit_test_source": "verified"})
    viral.label_category("demo", db_path=db_path)
    settled = prediction.settle_predictions("demo", db_path=db_path)
    assert settled["settled"] == 2 and settled["pending"] == 0

    # 样本不足:只报告不落库(用大 min_samples 控制,不碰真实配置)
    thin = prediction.prediction_report("demo", db_path=db_path, min_samples=99)
    assert thin["settled_samples"] == 2 and thin["mae"] is not None
    assert thin["asset_written"] is False and "不写知识资产" in thin["note"]

    report = prediction.prediction_report("demo", db_path=db_path, min_samples=2)
    assert report["asset_written"] is True
    assert report["mae"] is not None and report["mean_bias"] is not None

    # 重跑幂等:prediction_accuracy 资产始终一条
    prediction.prediction_report("demo", db_path=db_path, min_samples=2)
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT statement, confidence FROM knowledge_assets WHERE kind='prediction_accuracy'").fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "measured" and "平均绝对误差" in rows[0][0]
    finally:
        con.close()


def _rows(db_path, sql):
    con = sqlite3.connect(db_path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 关键词反哺


def _feedback_env(tmp_path, db_path, system_db):
    keywords.seed_keywords("demo", db_path=db_path)
    keywords.score_keywords("demo", db_path=db_path)
    return dict(db_path=db_path, system_db=system_db, evidence_dir=tmp_path / "fb")


def test_feedback_gap_enqueues_once_with_evidence(tmp_path):
    db_path = store.init_articles_db("demo", db_path=tmp_path / "demo.db")
    store.insert_items("demo", "test", [
        _item("人工智能动态", "人工智能行业新闻。", "https://example.com/f/1"),
    ], db_path=db_path)
    store.migrate_articles_db("demo", db_path=db_path)
    system_db = tmp_path / "sys.db"
    db.init_system_db(system_db)
    sources_path = _write_sources(tmp_path, [
        {"id": "formal_one", "name": "正式源一", "category": "demo", "kind": "rss",
         "url": "https://example.com/rss", "enabled": True, "demo": False},
        {"id": "demo_src", "name": "演示源", "category": "demo", "kind": "rss",
         "url": "https://example.com/d", "enabled": True, "demo": True},
        {"id": "other_cat", "name": "他类目", "category": "tech", "kind": "rss",
         "url": "https://example.com/t", "enabled": True, "demo": False},
        {"id": "disabled_src", "name": "停用源", "category": "demo", "kind": "rss",
         "url": "https://example.com/off", "enabled": False, "demo": False},
    ])
    kwargs = _feedback_env(tmp_path, db_path, system_db)
    result = keywords.auto_feedback("demo", sources_path=sources_path, **kwargs)

    assert "效率工具" in result["gap_keywords"]          # 未出现的种子词进 gap
    assert result["queued_job_id"] is not None           # 只对第一个正式启用来源入队
    jobs = _crawl_jobs(system_db)
    assert len(jobs) == 1
    assert jobs[0]["source"] == "formal_one" and jobs[0]["status"] == "queued"
    meta = json.loads(jobs[0]["meta_json"])
    assert meta["reason"] == "keyword_gap_feedback" and meta["dispatched_via"] == "keyword_feedback"

    # 去重:已有 queued 任务时不叠加
    second = keywords.auto_feedback("demo", sources_path=sources_path, **kwargs)
    assert second["queued_job_id"] == result["queued_job_id"]
    assert "不重复入队" in second["note"]
    assert len(_crawl_jobs(system_db)) == 1

    # 证据文件落盘且可读
    evidence = json.loads(_read_file(result["evidence_file"]))
    assert evidence["category"] == "demo" and evidence["gap_keywords"] == result["gap_keywords"]

    # gap 词恢复覆盖 → 回到 active
    store.insert_items("demo", "test", [
        _item("效率工具盘点", "效率工具年度盘点文章。", "https://example.com/f/2"),
    ], db_path=db_path)
    keywords.score_keywords("demo", db_path=db_path)
    third = keywords.auto_feedback("demo", sources_path=sources_path, **kwargs)
    assert third["recovered_keywords"] >= 1 and "效率工具" not in third["gap_keywords"]
    con = sqlite3.connect(db_path)
    try:
        status = con.execute("SELECT status FROM keywords WHERE keyword='效率工具'").fetchone()[0]
        assert status == "active"
    finally:
        con.close()


def _read_file(path):
    from pathlib import Path
    return Path(path).read_text(encoding="utf-8")


def test_feedback_demo_category_never_enqueues(tmp_path):
    db_path = store.init_articles_db("demo", db_path=tmp_path / "demo.db")
    store.insert_items("demo", "test", [
        _item("人工智能动态", "人工智能行业新闻。", "https://example.com/f/1"),
    ], db_path=db_path)
    store.migrate_articles_db("demo", db_path=db_path)
    system_db = tmp_path / "sys.db"
    db.init_system_db(system_db)
    sources_path = _write_sources(tmp_path, [
        {"id": "demo_only", "name": "演示源", "category": "demo", "kind": "rss",
         "url": "https://example.com/d", "enabled": True, "demo": True},
    ])
    kwargs = _feedback_env(tmp_path, db_path, system_db)
    result = keywords.auto_feedback("demo", sources_path=sources_path, **kwargs)
    assert result["gap_keywords"] and result["queued_job_id"] is None
    assert "无正式启用来源" in result["note"]
    assert _crawl_jobs(system_db) == []                 # 演示类目绝不入队


# ---------------------------------------------------------------------------
# 调度器


def test_scheduler_forbidden_kinds_rejected():
    conf = {"scheduler": {"enabled": True, "jobs": [
        {"id": "j1", "kind": "write", "interval_min": 60},
    ]}}
    with pytest.raises(ValueError):
        scheduler.load_jobs(conf)
    with pytest.raises(ValueError):
        scheduler.run_job({"id": "j1", "kind": "publish"})


def test_scheduler_due_and_disabled(tmp_path):
    now = datetime(2026, 11, 12, 12, 0, 0)
    job = {"id": "j", "kind": "recover", "interval_min": 60, "enabled": True,
           "system_db": str(tmp_path / "none.db")}
    assert scheduler.is_due(job, {}, now) is True
    assert scheduler.is_due(job, {"j": {"last_run_at": (now - timedelta(minutes=90)).isoformat()}}, now)
    assert not scheduler.is_due(job, {"j": {"last_run_at": (now - timedelta(minutes=30)).isoformat()}}, now)
    disabled = scheduler.run_due(state_path=tmp_path / "state.json",
                                 conf={"scheduler": {"enabled": False, "jobs": []}})
    assert disabled["skipped"] == "scheduler.disabled" and disabled["ran"] == []


def test_scheduler_offsets_are_safe(tmp_path):
    """状态里带时区偏移的 last_run_at 不得让第二次运行崩溃(历史 bug)。"""
    now = datetime(2026, 11, 12, 12, 0, 0)
    job = {"id": "j", "kind": "recover", "interval_min": 60, "enabled": True,
           "system_db": str(tmp_path / "none.db")}
    old = (now - timedelta(minutes=90)).astimezone(timezone.utc).isoformat()
    fresh = (now - timedelta(minutes=30)).astimezone(timezone.utc).isoformat()
    assert scheduler.is_due(job, {"j": {"last_run_at": old}}, now)
    assert not scheduler.is_due(job, {"j": {"last_run_at": fresh}}, now)


def test_scheduler_run_due_isolates_failures(tmp_path):
    state_path = tmp_path / "state.json"
    conf = {"scheduler": {"enabled": True, "jobs": [
        {"id": "bad_runner", "kind": "no_such_kind", "interval_min": 60, "enabled": True},
        {"id": "missing_sources", "kind": "crawl_enqueue", "interval_min": 60, "enabled": True,
         "system_db": str(tmp_path / "sys.db"), "sources_path": str(tmp_path / "nope.json")},
        {"id": "off_job", "kind": "recover", "interval_min": 60, "enabled": False,
         "system_db": str(tmp_path / "sys.db")},
    ]}}
    result = scheduler.run_due(state_path=state_path, conf=conf)
    statuses = {entry["id"]: entry["status"] for entry in result["ran"]}
    assert statuses == {"bad_runner": "error", "missing_sources": "error"}
    assert result["skipped_not_due"] == 1               # 停用任务不执行
    state = json.loads(_read_file(state_path))
    assert state["bad_runner"]["last_status"] == "error" and state["bad_runner"]["last_error"]

    # 到期判断在 status_report 中一致
    report = scheduler.status_report(state_path=state_path, conf=conf)
    rows = {row["id"]: row for row in report["jobs"]}
    assert rows["bad_runner"]["due"] is False           # 刚跑过
    assert rows["off_job"]["due"] is False              # 停用永不 due


# ---------------------------------------------------------------------------
# 面板任务下发护栏


class _Req(server.DashboardHandler):
    """脱离网络的 Handler 实例,仅用于安全护栏单测。"""

    def __init__(self, headers: dict, body: bytes = b"{}"):
        self.headers = dict(headers)
        self.rfile = io.BytesIO(body)

    def _send_json(self, *a, **k):  # pragma: no cover - 单测不发送
        raise AssertionError("不该发生网络发送")


def test_task_guard_matrix():
    ok = {"X-TTXT-Task": "1", "Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"}
    assert _Req(ok)._task_allowed() is None
    assert _Req({**ok, "Origin": "http://127.0.0.1:8765/"})._task_allowed() is None
    assert _Req({"X-TTXT-Task": "1", "Host": "localhost:8765"})._task_allowed() is None
    assert _Req({"X-TTXT-Task": "1", "Host": "[::1]:8765",
                 "Origin": "http://[::1]:8765"})._task_allowed() is None
    # 缺任务头
    assert "X-TTXT-Task" in _Req({"Host": "127.0.0.1:8765"})._task_allowed()
    # DNS rebinding:非回环 Host
    assert "回环" in _Req({**ok, "Host": "attacker.example:8765"})._task_allowed()
    # Origin 不匹配
    assert "Origin" in _Req({**ok, "Origin": "http://evil.example"})._task_allowed()
    # Origin 空串也必须给出 expected 匹配
    assert _Req({"X-TTXT-Task": "1", "Origin": ""})._task_allowed() is not None


def test_json_body_limits():
    handler = _Req({"Content-Length": "5"}, b'{"a"1')
    with pytest.raises(ValueError):
        handler._read_json_body()
    handler = _Req({"Content-Length": "2"}, b"[1]")
    with pytest.raises(ValueError):
        handler._read_json_body()
    handler = _Req({"Content-Length": str(65 * 1024)}, b"{}")
    with pytest.raises(ValueError):
        handler._read_json_body()


def test_enqueue_crawl_validation_and_dedupe(tmp_path, monkeypatch):
    sources_path = _write_sources(tmp_path, [
        {"id": "formal", "name": "正式源", "category": "technology", "kind": "rss",
         "url": "https://example.com/rss", "enabled": True, "demo": False},
    ])
    system_db = tmp_path / "sys.db"
    db.init_system_db(system_db)
    from ttxt.crawler import store as crawler_store
    monkeypatch.setattr(source_loader, "SOURCES_PATH", sources_path)
    monkeypatch.setattr(crawler_store, "system_db_path", lambda: system_db)

    with pytest.raises(ValueError):
        api.enqueue_crawl("")
    with pytest.raises(ValueError):
        api.enqueue_crawl("ghost")
    with pytest.raises(ValueError):
        api.enqueue_crawl("formal", "finance")          # 路由违规:拒绝跨类目

    first = api.enqueue_crawl("formal")
    assert first["category"] == "technology" and first["deduped"] is False
    jobs = _crawl_jobs(system_db)
    assert len(jobs) == 1 and json.loads(jobs[0]["meta_json"])["dispatched_via"] == "dashboard"

    second = api.enqueue_crawl("formal")
    assert second["deduped"] is True and second["job_id"] == first["job_id"]
    assert len(_crawl_jobs(system_db)) == 1


def test_analyze_task_rejects_unknown_category():
    with pytest.raises(ValueError):
        api.run_analyze_task("")
    with pytest.raises(ValueError):
        api.run_analyze_task("no-such-category-x")      # 不在配置且无本地库


def test_sources_and_task_queue_shapes(tmp_path, monkeypatch):
    sources_path = _write_sources(tmp_path, [
        {"id": "s1", "name": "S1", "category": "technology", "kind": "rss",
         "url": "https://example.com/a", "enabled": True, "demo": False},
    ])
    monkeypatch.setattr(source_loader, "SOURCES_PATH", sources_path)
    listed = api.sources()
    assert listed and listed[0]["id"] == "s1" and listed[0]["enabled"] is True

    system_db = tmp_path / "sys.db"
    db.init_system_db(system_db)
    from ttxt.crawler import store as crawler_store
    monkeypatch.setattr(crawler_store, "system_db_path", lambda: system_db)
    from ttxt.dashboard import api as api_module
    monkeypatch.setattr(api_module, "system_db_path", lambda: system_db)
    monkeypatch.setattr(scheduler, "status_report",
                        lambda **kw: {"enabled": True, "jobs": []})
    queue = api.task_queue(limit=5)
    assert queue["pending"] == [] and queue["schedule"]["enabled"] is True


def test_dashboard_post_routes_reject_everything_else():
    """do_POST 白名单:非 /api/tasks/* 一律 405(用轻量替身捕获状态码)。"""
    sent: list[tuple] = []

    class Probe(server.DashboardHandler):
        def __init__(self, path):
            self.path = path
            self.headers = {}
            self.wfile = io.BytesIO()

        def _send_json(self, payload, status=200):
            sent.append((status, payload))

        def _send_error_json(self, status, message):
            sent.append((status, {"error": message}))

    Probe("/api/other").do_POST()
    assert sent[-1][0] == 405 and "405" not in str(sent[-1][1])
    probe = Probe("/api/tasks/write")
    probe._task_allowed = lambda: None                      # type: ignore[method-assign]
    probe.rfile = io.BytesIO(b"{}")
    probe._read_json_body = lambda: {}                      # type: ignore[method-assign]
    probe.do_POST()
    assert sent[-1][0] == 404 and "写作" in sent[-1][1]["error"]
