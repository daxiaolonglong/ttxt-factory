# -*- coding: utf-8 -*-
"""R65(2026-11-21):挖词必须进采集闭环,而且采不动要认账。

实测根因(2026-11-21 全类目只读探针 ``07_tmp/r65_harvest/harvest.json``):
五个类目 38 个 ``origin='mined'`` 关键词里 **30 个自被提出之日起一篇新文章都没带回来**
(social 10 个全部为 0,含标题命中 399 篇的「闷声发财」;finance 9/10 为 0;health 唯一挖词 0),
可它们的 ``collected`` 全是 6~399 —— 因为 ``collected`` 的口径是"**全库标题命中**",
而挖词本来就是从这批标题里挖出来的 ⇒ 挖词天然不等于零覆盖 ⇒ 永远进不了 ``gap`` ⇒
``auto_feedback`` 从来没为它们入过一次队。目标②里"反馈关键词给模型继续采集"这一环
在挖词侧是断的(scheduler 的 4h 入队是**整源**任务,带 ``keyword_filter`` 的定向采集只有
``auto_feedback`` 一条路,而它只认 gap)。

本轮加的口径(不放宽任何既有阈值):
- 新指标 ``keyword_performance.harvested`` = 标题命中(别名归一后)**且** ``articles.fetched_at``
  晚于 ``keywords.created_at`` 的篇数 ⇒ "这个词被提出之后,采集侧真的带回了多少篇";
  时间口径读不动时记 ``NULL``(不可判定),**不等于** 0,退场判定只认 0;
- ``harvested = 0`` 的挖词进同一轮反哺的 ``keyword_filter``(与 gap 同一个来源、同一冷却窗口),
  逐词定向次数记在类目库 ``dispatch_state.keyword_feedback.per_keyword``;
- 真实入队满 ``MINE_FEEDBACK_MAX_ATTEMPTS`` 次仍是 0 ⇒ ``status='exhausted'`` 退场:
  不删关键词行、不删评分行(采不动是学到的负面知识),采集计划不再选它,但写作主题照旧收它;
  复活凭证只有一个 —— ``harvested > 0``;
- 已被证明"采得动"的挖词(``harvested > 0``)不因本轮没被 top_n 选中就被 prune 删掉。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from ttxt import db
from ttxt.analysis import keywords, topics
from ttxt.crawler import keyword_crawl, store
from ttxt.crawler.extract import ExtractedItem


def _iso(moment: datetime) -> str:
    return moment.astimezone().isoformat(timespec="seconds")


def _before(now: datetime, days: int) -> str:
    return _iso(now - timedelta(days=days))


def _item(title: str, url: str) -> ExtractedItem:
    return ExtractedItem(title=title, content="正文内容用于满足清洗长度要求。" * 5, excerpt="",
                         author="", published_at=None, url=url)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _env(tmp_path) -> tuple[dict, Path]:
    """两个挖词(一个采得动、一个采不动)+ 一个真正的 gap 种子词。

    建词时间钉在 10 天前:``血糖仪`` 只有一篇**建词之前**的命中文章 ⇒ harvested=0;
    ``胰岛素`` 在建词**之后**又采回一篇 ⇒ harvested=1。``涨停`` 全库零命中 ⇒ 走原有 gap 通道。
    """
    db_path = store.init_articles_db("demo", db_path=tmp_path / "demo.db")
    store.migrate_articles_db("demo", db_path=db_path)      # keywords/keyword_performance 都在迁移里建
    now = datetime.now().astimezone()
    created = _before(now, 10)
    store.insert_items("demo", "src", [
        _item("家用血糖仪选购指南", "https://example.com/h/1"),   # 建词之前的存量命中
    ], db_path=db_path)
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "UPDATE articles SET fetched_at=? WHERE title LIKE '%血糖仪%'", (_before(now, 20),))
        for word in ("血糖仪", "胰岛素"):
            con.execute(
                "INSERT INTO keywords(keyword, category, origin, status, created_at)"
                " VALUES(?, 'demo', 'mined', 'active', ?)", (word, created))
        con.commit()
    finally:
        con.close()
    store.insert_items("demo", "src", [
        _item("胰岛素降价了吗", "https://example.com/h/2"),        # 建词之后真的采回来了
    ], db_path=db_path)
    keywords.seed_keywords("demo", db_path=db_path)               # 种子里含零命中词 ⇒ gap
    store.migrate_articles_db("demo", db_path=db_path)
    keywords.score_keywords("demo", db_path=db_path)

    system_db = tmp_path / "sys.db"
    db.init_system_db(system_db)
    sources_path = _write(tmp_path / "sources.json", json.dumps({"sources": [
        {"id": "formal_one", "name": "正式源一", "category": "demo", "kind": "rss",
         "url": "https://example.com/rss", "enabled": True, "demo": False},
    ]}, ensure_ascii=False))
    kwargs = dict(db_path=db_path, system_db=system_db, evidence_dir=tmp_path / "fb")
    return kwargs, sources_path


def _perf(db_path: Path) -> dict[str, dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return {
            row["keyword"]: {
                "status": row["status"], "origin": row["origin"],
                "collected": row["collected"], "harvested": row["harvested"],
                "row_id": row["id"],
            }
            for row in con.execute(
                "SELECT k.id, k.keyword, k.status, k.origin, p.collected, p.harvested"
                " FROM keywords k LEFT JOIN keyword_performance p ON p.keyword_id = k.id")
        }
    finally:
        con.close()


def _filter_of_job(system_db: Path, job_id: int) -> list[str]:
    con = sqlite3.connect(system_db)
    try:
        meta = json.loads(con.execute("SELECT meta_json FROM crawl_jobs WHERE id=?", (job_id,)).fetchone()[0])
    finally:
        con.close()
    assert meta["reason"] == "keyword_gap_feedback"
    assert meta["keyword_filter"] == meta["keywords"], "两个键必须同源,否则入库闸门与计划不一致"
    return list(meta["keyword_filter"])


def _drain(job_id, system_db):
    store.finish_crawl_job(job_id, "done", meta={"pages_fetched": 2, "inserted": 0, "duplicates": 7},
                           db_path=system_db)


def _set_harvest(db_path: Path, keyword: str, value: int) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "UPDATE keyword_performance SET harvested=? WHERE keyword_id=(SELECT id FROM keywords WHERE keyword=?)",
            (value, keyword))
        con.commit()
    finally:
        con.close()


def _drive_to_exhaustion(kwargs: dict, sources_path: Path) -> dict:
    """跑满 ``MINE_FEEDBACK_MAX_ATTEMPTS`` 次**真实入队 + 回执**,再走一轮复审。

    口径(与实现一致):第 3 次补采的结果要等采集跑完、下一轮 analyze 才读得到
    ⇒ 退场判定发生在"看结果"的那一轮,不能在入队当轮提前判死。
    """
    for _ in range(keywords.MINE_FEEDBACK_MAX_ATTEMPTS):
        fb = keywords.auto_feedback("demo", sources_path=sources_path, cooldown_hours=0.0, **kwargs)
        assert fb["queued_job_id"] is not None, f"第 {_} 次应当真的入队"
        _drain(fb["queued_job_id"], kwargs["system_db"])
    assert _perf(kwargs["db_path"])["血糖仪"]["status"] == "active", \
        "刚排满 3 次的那一轮不得提前判死:最后一次采集结果还没读到"
    return keywords.auto_feedback("demo", sources_path=sources_path, cooldown_hours=0.0, **kwargs)


# --------------------------------------------------------------- harvested 口径本身

def test_harvest_column_added_by_migration_without_touching_rows(tmp_path):
    """老库只加列:既有评分行一律保留,新列一律 ``NULL``(没算过 ≠ 采不到)。"""
    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE keywords(id INTEGER PRIMARY KEY, keyword TEXT, category TEXT,"
                    " origin TEXT, status TEXT, created_at TEXT)")
        con.execute("CREATE TABLE keyword_performance(keyword_id INTEGER, collected INTEGER,"
                    " quality_articles INTEGER, avg_text_length REAL, score REAL,"
                    " last_used_at TEXT, updated_at TEXT NOT NULL)")
        con.execute("INSERT INTO keywords(keyword, category, origin, status, created_at)"
                    " VALUES('老词', 'demo', 'seed', 'active', '2026-11-01T08:00:00+08:00')")
        con.execute("INSERT INTO keyword_performance(keyword_id, collected, quality_articles,"
                    " avg_text_length, score, updated_at) VALUES(1, 12, 3, 800.0, 0.5,"
                    " '2026-11-10T08:00:00+08:00')")
        con.commit()
    finally:
        con.close()

    store.migrate_articles_db("demo", db_path=path)
    con = sqlite3.connect(path)
    try:
        columns = {row[1] for row in con.execute("PRAGMA table_info(keyword_performance)")}
        rows = con.execute("SELECT collected, score, harvested FROM keyword_performance").fetchall()
    finally:
        con.close()
    assert "harvested" in columns
    assert rows == [(12, 0.5, None)], "迁移不得改动既有评分行,也不得把没算过的词当成采不到"
    store.migrate_articles_db("demo", db_path=path)  # 幂等


def test_score_keywords_measures_harvest_separately_from_collected(tmp_path):
    """collected 证明不了词表扩张(挖词本来就来自这批标题);harvested 才证明。"""
    kwargs, _ = _env(tmp_path)
    stats = keywords.score_keywords("demo", db_path=kwargs["db_path"])
    perf = _perf(kwargs["db_path"])

    assert perf["血糖仪"]["collected"] >= 1 and perf["血糖仪"]["harvested"] == 0, \
        "存量命中不算扩张:建词之后一篇没采回来,harvested 必须是 0"
    assert perf["胰岛素"]["harvested"] >= 1, "建词之后采回来的标题命中要计入 harvested"
    assert stats["harvest"]["column_tracked"] is True
    assert stats["harvest"]["never_harvested"] >= 1 and stats["harvest"]["with_harvest"] >= 1
    details = stats["viral"]["details"]["血糖仪"]
    assert details["harvested_after_keyword"] == 0
    assert details["matched_articles"] >= 1, "collected(覆盖口径)仍是那个数:harvested 只新增,不改老口径"


def test_unreadable_timestamps_are_unknown_not_zero(tmp_path):
    """fetched_at 读不动 ⇒ harvested=NULL(不可判定),不能进退场判定。"""
    kwargs, sources_path = _env(tmp_path)
    db_path = kwargs["db_path"]
    con = sqlite3.connect(db_path)
    try:
        con.execute("UPDATE articles SET fetched_at='昨天' WHERE 1=1")
        con.execute("UPDATE keywords SET created_at='不知道' WHERE keyword='血糖仪'")
        con.commit()
    finally:
        con.close()
    keywords.score_keywords("demo", db_path=db_path)
    assert _perf(db_path)["血糖仪"]["harvested"] is None

    fb = keywords.auto_feedback("demo", sources_path=sources_path, **kwargs)
    assert [item["keyword"] for item in fb["harvest_feedback"]["unharvested_mined_keywords"]] == ["胰岛素"], \
        "不可判定的词不得被当成采不动"


# ----------------------------------------------------- 反哺:harvested=0 的挖词进定向过滤

def test_unharvested_mined_keyword_gets_directed_filter(tmp_path):
    """R65 的断点修复:没有 gap 词的挖词侧也必须能定向补采。"""
    kwargs, sources_path = _env(tmp_path)
    fb = keywords.auto_feedback("demo", sources_path=sources_path, **kwargs)
    assert fb["queued_job_id"] is not None
    wanted = _filter_of_job(kwargs["system_db"], fb["queued_job_id"])
    assert "血糖仪" in wanted, "harvested=0 的挖词必须进这一轮的 keyword_filter"
    assert "胰岛素" not in wanted, "采得动的词不需要再定向"
    assert set(fb["gap_keywords"]) <= set(wanted), "原有 gap 通道不能被这次改动挤掉"
    assert fb["harvest_feedback"]["directed_keywords_in_filter"] == ["血糖仪"]
    assert fb["harvest_feedback"]["exhausted_keywords"] == []

    state = store.load_dispatch_state(kwargs["db_path"])
    per_keyword = state["keyword_feedback"][keywords.PER_KEYWORD_STATE_KEY]
    assert per_keyword["血糖仪"][keywords.DIRECTED_ATTEMPTS_FIELD] == 1
    assert "胰岛素" not in per_keyword, "采得动的词不进定向名单,也就没有次数账"


def test_directed_attempts_count_only_real_enqueues(tmp_path):
    """排队去重命中(没真入队)不计数 —— 否则一轮没执行就把词判死。"""
    kwargs, sources_path = _env(tmp_path)
    first = keywords.auto_feedback("demo", sources_path=sources_path, **kwargs)
    second = keywords.auto_feedback("demo", sources_path=sources_path, **kwargs)
    assert second["queued_job_id"] == first["queued_job_id"]
    assert "不重复入队" in second["note"]
    state = store.load_dispatch_state(kwargs["db_path"])
    per_keyword = state["keyword_feedback"][keywords.PER_KEYWORD_STATE_KEY]
    assert per_keyword["血糖仪"][keywords.DIRECTED_ATTEMPTS_FIELD] == 1, "第二次没入队 ⇒ 次数不变"


def test_exhaustion_after_max_directed_attempts(tmp_path):
    """定向满 3 次仍是 0 ⇒ 认账退场:状态改 exhausted,关键词行与评分行都留着。"""
    kwargs, sources_path = _env(tmp_path)
    after = _drive_to_exhaustion(kwargs, sources_path)
    assert after["harvest_feedback"]["exhausted_keywords"] == ["血糖仪"]
    assert "血糖仪" not in after["harvest_feedback"]["directed_keywords_in_filter"]
    perf = _perf(kwargs["db_path"])
    assert perf["血糖仪"]["status"] == keywords.MINE_STATUS_EXHAUSTED
    assert perf["血糖仪"]["row_id"] is not None, "退场不删关键词行"
    assert perf["血糖仪"]["harvested"] == 0, "退场不删评分行:采不动是学到的负面知识"

    # 退场之后不再为它入队:这一轮的定向名单里只剩 gap 词(有 gap 才会有任务)
    if after["queued_job_id"] is not None:
        wanted = _filter_of_job(kwargs["system_db"], after["queued_job_id"])
        assert "血糖仪" not in wanted and set(wanted) == set(after["gap_keywords"])
    else:
        assert after["gap_keywords"] == [], "退场后没别的词要定向 ⇒ 不该再入队"

    # 且退场是稳定的(不会被下一轮又拉回定向名单)
    third = keywords.auto_feedback("demo", sources_path=sources_path, cooldown_hours=0.0, **kwargs)
    assert third["harvest_feedback"]["unharvested_mined_keywords"] == []


def test_exhausted_word_revives_only_when_harvest_proves_it(tmp_path):
    """复活凭证只有一个:harvested>0(语料里真的因为这个词多出了文章)。"""
    kwargs, sources_path = _env(tmp_path)
    _drive_to_exhaustion(kwargs, sources_path)
    assert _perf(kwargs["db_path"])["血糖仪"]["status"] == keywords.MINE_STATUS_EXHAUSTED

    _set_harvest(kwargs["db_path"], "血糖仪", 3)
    fb = keywords.auto_feedback("demo", sources_path=sources_path, cooldown_hours=0.0, **kwargs)
    assert fb["harvest_feedback"]["revived_mined_keywords"] == 1
    assert _perf(kwargs["db_path"])["血糖仪"]["status"] == "active"
    assert "血糖仪" not in fb["harvest_feedback"]["unharvested_mined_keywords"]


# ------------------------------------------------- 退场的作用面:采集侧关,写作侧开

def test_exhausted_keywords_leave_crawl_plan_but_stay_topics(tmp_path):
    """退场只停采集预算:语料里本来就有几百篇的词,仍然是能写的主题。"""
    kwargs, sources_path = _env(tmp_path)
    _drive_to_exhaustion(kwargs, sources_path)
    db_path = kwargs["db_path"]
    assert _perf(db_path)["血糖仪"]["status"] == keywords.MINE_STATUS_EXHAUSTED

    planned = [row["keyword"] for row in keyword_crawl.category_keywords("demo", db_path=db_path, limit=20)]
    assert "血糖仪" not in planned, "采不动的词不得再进采集计划(白烧限速预算)"

    cands = topics.keyword_candidates("demo", db_path=db_path, limit=20)
    by_word = {item["ref"]: item for item in cands["items"]}
    assert "血糖仪" in by_word, "退场≠不能写:标题命中摆在那里,主题候选照旧收"
    assert by_word["血糖仪"]["keyword_status"] == keywords.MINE_STATUS_EXHAUSTED
    assert by_word["胰岛素"]["keyword_status"] == "active"


# ------------------------------------------------------ 采得动的挖词不被 prune 删掉

def test_proven_mined_words_survive_prune(tmp_path):
    """prune 只清"这轮没产出且没战功"的旧挖词;harvested>0 的留着(目标④)。

    旧词直接写进表里(等价于"上一轮算法留下的挖词"):本轮 top_n=0 ⇒ 一个都不选,
    于是没战功的必须退场,有战功(harvested=4)的必须活下来。
    """
    from test_r28_keyword_mining import CHIP_TITLES, _calibrate, _fillers, _keyword_rows, _make_db, _mark_viral

    titles = CHIP_TITLES + _fillers(24)
    db_path = _make_db(tmp_path, titles)
    _mark_viral(db_path, list(range(1, 7)))
    _calibrate(db_path)

    mined = keywords.mine_keywords("demo", db_path=db_path, top_n=50, background=[])
    assert mined["status"] == "ok"
    words = [word for word, origin in _keyword_rows(db_path) if origin == keywords.MINE_ORIGIN]
    assert "国产芯片" in words
    con = sqlite3.connect(db_path)
    try:
        con.execute("INSERT INTO keywords(keyword, category, origin, status, created_at)"
                    " VALUES('上一轮的老词', 'demo', 'mined', 'active', '2026-11-01T08:00:00+08:00')")
        con.commit()
    finally:
        con.close()

    keywords.score_keywords("demo", db_path=db_path)
    _set_harvest(db_path, "国产芯片", 4)

    second = keywords.mine_keywords("demo", db_path=db_path, top_n=0, background=[])
    assert second["pruned"] >= 1, "没战功的旧挖词照旧退场"
    assert second["proven_mined_kept"] >= 1
    survivors = [word for word, origin in _keyword_rows(db_path) if origin == keywords.MINE_ORIGIN]
    assert "上一轮的老词" not in survivors
    assert "国产芯片" in survivors, "只有被证明采得动的挖词能活过退场"
