# -*- coding: utf-8 -*-
"""P26 ⓪:关键词反哺入队必须有冷却窗口(2026-11-16 实测的无界循环)。

实测根因(见 ``08_wiki/reports/P25-two-new-channels-and-fts-index-20261116.md`` §十五 与
P17 §十六 补记 ⑪):同一天里 ``actor=keyword_feedback`` 入队 4 次(job 74/75/76/77),
gap 词始终是那两个(technology ``效率工具``、health ``睡眠``);health 那一支每次执行结果都是
``inserted 0 / duplicates 25``,gap 永远补不上,却每轮重新排队 ⇒ 白烧限速预算、队列永不清空。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ttxt import db
from ttxt.analysis import keywords
from ttxt.crawler import store
from ttxt.crawler.extract import ExtractedItem


def _item(title: str, content: str, url: str) -> ExtractedItem:
    return ExtractedItem(title=title, content=content, excerpt="", author="", published_at=None, url=url)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _env(tmp_path):
    """一条有覆盖的文章 + 一个正式来源 ⇒ 种子词里必然长出 gap 词(p11 已证)。"""
    db_path = store.init_articles_db("demo", db_path=tmp_path / "demo.db")
    store.insert_items("demo", "test", [
        _item("人工智能动态", "人工智能行业今日新闻。", "https://example.com/f/1"),
    ], db_path=db_path)
    store.migrate_articles_db("demo", db_path=db_path)
    system_db = tmp_path / "sys.db"
    db.init_system_db(system_db)
    sources_path = _write(tmp_path / "sources.json", json.dumps({"sources": [
        {"id": "formal_one", "name": "正式源一", "category": "demo", "kind": "rss",
         "url": "https://example.com/rss", "enabled": True, "demo": False},
    ]}, ensure_ascii=False))
    keywords.seed_keywords("demo", db_path=db_path)
    keywords.score_keywords("demo", db_path=db_path)
    return dict(db_path=db_path, system_db=system_db, evidence_dir=tmp_path / "fb"), sources_path


def _jobs(system_db):
    con = sqlite3.connect(system_db)
    try:
        return [dict(zip(("id", "source", "status", "meta_json"), row)) for row in con.execute(
            "SELECT id, source, status, meta_json FROM crawl_jobs ORDER BY id")]
    finally:
        con.close()


def _log_events() -> list[dict]:
    """读 `09_logs/*.jsonl`(与 p25 可观测性测试同一手法)。"""
    from ttxt import config as cfg

    events: list[dict] = []
    log_dir = Path(cfg.LOG_DIR)
    if not log_dir.exists():
        return events
    for path in sorted(log_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


def _finish_like_real_drain(job_id, system_db):
    """按真实 Worker 的方式收尾:``finish_crawl_job`` 会用执行回执**整体覆盖** ``meta_json``,
    入队时写的 ``dispatched_via`` 就此消失 —— 所以冷却状态绝不能寄存在 crawl_jobs 上。"""
    store.finish_crawl_job(job_id, "done", meta={"pages_fetched": 3, "inserted": 0, "duplicates": 25},
                           db_path=system_db)


def test_second_feedback_run_inside_cooldown_must_not_enqueue(tmp_path):
    kwargs, sources_path = _env(tmp_path)
    first = keywords.auto_feedback("demo", sources_path=sources_path, **kwargs)
    assert first["gap_keywords"] and first["queued_job_id"] is not None

    _finish_like_real_drain(first["queued_job_id"], kwargs["system_db"])

    second = keywords.auto_feedback("demo", sources_path=sources_path, **kwargs)
    assert second["gap_keywords"], "gap 词还在(采不到就是采不到),但不能再入队"
    assert second["queued_job_id"] is None
    assert "冷却" in second["note"]
    assert second["cooldown_remaining_hours"] is not None and second["cooldown_remaining_hours"] > 0
    assert second["last_enqueued_at"] == first["generated_at"] or second["last_enqueued_at"] is not None
    assert second["feedback_attempts"] == 2
    assert len(_jobs(kwargs["system_db"])) == 1, "冷却窗口内不得新增任务"

    third = keywords.auto_feedback("demo", sources_path=sources_path, **kwargs)
    assert third["queued_job_id"] is None and third["feedback_attempts"] == 3
    assert len(_jobs(kwargs["system_db"])) == 1


def test_cooldown_state_survives_job_meta_overwrite(tmp_path):
    """冷却状态存在类目库 ``articles_meta``,不靠 ``crawl_jobs.meta_json``(会被回执覆盖)。"""
    kwargs, sources_path = _env(tmp_path)
    first = keywords.auto_feedback("demo", sources_path=sources_path, **kwargs)
    job_id = first["queued_job_id"]
    con = sqlite3.connect(kwargs["system_db"])
    try:
        meta_before = json.loads(con.execute("SELECT meta_json FROM crawl_jobs WHERE id=?", (job_id,)).fetchone()[0])
    finally:
        con.close()
    assert meta_before["dispatched_via"] == "keyword_feedback"

    _finish_like_real_drain(job_id, kwargs["system_db"])
    con = sqlite3.connect(kwargs["system_db"])
    try:
        meta_after = json.loads(con.execute("SELECT meta_json FROM crawl_jobs WHERE id=?", (job_id,)).fetchone()[0])
    finally:
        con.close()
    assert "dispatched_via" not in meta_after, "回执覆盖了入队时的 provenance(这正是不能靠它做冷却的原因)"

    con = sqlite3.connect(kwargs["db_path"])
    try:
        raw = con.execute("SELECT value FROM articles_meta WHERE key='dispatch_state'").fetchone()
    finally:
        con.close()
    assert raw is not None, "冷却状态必须落在类目库 articles_meta.dispatch_state"
    state = json.loads(raw[0])
    assert state["keyword_feedback"]["formal_one"]["last_enqueued_at"]

    second = keywords.auto_feedback("demo", sources_path=sources_path, **kwargs)
    assert second["queued_job_id"] is None and "冷却" in second["note"]


def test_cooldown_zero_hours_requeues_every_run(tmp_path):
    """运维逃生阀:``cooldown_hours=0`` 恢复旧行为(每轮都入队),测试与紧急补采要用。"""
    kwargs, sources_path = _env(tmp_path)
    first = keywords.auto_feedback("demo", sources_path=sources_path, cooldown_hours=0.0, **kwargs)
    _finish_like_real_drain(first["queued_job_id"], kwargs["system_db"])
    second = keywords.auto_feedback("demo", sources_path=sources_path, cooldown_hours=0.0, **kwargs)
    assert second["queued_job_id"] is not None
    assert len(_jobs(kwargs["system_db"])) == 2


def test_feedback_enqueue_log_carries_attempt_number(tmp_path):
    """验收口径(P25 §十三 ⓪):入队日志要能看出"第几次尝试",否则排队动作下一轮仍然无法归因。"""
    kwargs, sources_path = _env(tmp_path)
    first = keywords.auto_feedback("demo", sources_path=sources_path, cooldown_hours=0.0, **kwargs)
    hits = [event for event in _log_events()
            if event.get("event") == "crawl.enqueued" and (event.get("meta") or {}).get("job_id") == first["queued_job_id"]]
    assert hits, "入队必须留下 crawl.enqueued 事件"
    meta = hits[-1]["meta"]
    assert meta["actor"] == "keyword_feedback"
    assert int(meta["attempt"]) == 1, "日志要能看出这是第几次反哺尝试"

    _finish_like_real_drain(first["queued_job_id"], kwargs["system_db"])
    second = keywords.auto_feedback("demo", sources_path=sources_path, cooldown_hours=0.0, **kwargs)
    hits2 = [event for event in _log_events()
             if event.get("event") == "crawl.enqueued" and (event.get("meta") or {}).get("job_id") == second["queued_job_id"]]
    assert int(hits2[-1]["meta"]["attempt"]) == 2


def test_pending_job_dedupe_still_wins_before_cooldown(tmp_path):
    """已有 queued/running 任务时仍走 p11 的去重通道,不能变成"冷却"文案(语义不同,排障时会误导)。"""
    kwargs, sources_path = _env(tmp_path)
    first = keywords.auto_feedback("demo", sources_path=sources_path, **kwargs)
    second = keywords.auto_feedback("demo", sources_path=sources_path, **kwargs)
    assert second["queued_job_id"] == first["queued_job_id"]
    assert "不重复入队" in second["note"] and "冷却" not in second["note"]
    assert len(_jobs(kwargs["system_db"])) == 1
