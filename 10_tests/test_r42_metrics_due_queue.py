"""R42:表现采集按 ``due_at`` 排队 —— 到点必采,覆盖 pending / late 积压。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from ttxt.analysis import metrics_import, metrics_queue
from ttxt.crawler import metrics_pages
from ttxt.crawler import store as crawler_store
from ttxt.writing import publication

CATEGORY = "r42"
SPAN = 3


def _iso(offset_hours: float = 0.0) -> str:
    return (datetime.now().astimezone() + timedelta(hours=offset_hours)).isoformat(timespec="seconds")


def _db(tmp_path):
    return crawler_store.migrate_articles_db(CATEGORY, db_path=tmp_path / "articles.db")


def _insert_article(db_path, title: str, url: str, published_at: str) -> int:
    from ttxt.db import connect

    con = connect(db_path)
    try:
        cur = con.execute(
            "INSERT INTO articles(category, source, title, content, published_at, url,"
            " url_normalized, fetched_at, content_hash) VALUES(?,?,?,?,?,?,?,?,?)",
            (CATEGORY, "test", title, "正文" * 50, published_at, url, url, _iso(), f"hash-{title}"),
        )
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def _insert_snapshot(db_path, article_id: int, captured_at: str, **metrics) -> None:
    from ttxt.db import connect

    con = connect(db_path)
    try:
        con.execute(
            "INSERT INTO metric_snapshots(article_id, captured_at, reads, likes, favorites,"
            " comments, impressions, data_source) VALUES(?,?,?,?,?,?,?,?)",
            (
                article_id,
                captured_at,
                metrics.get("reads"),
                metrics.get("likes"),
                metrics.get("favorites"),
                metrics.get("comments"),
                metrics.get("impressions"),
                "manual_verified",
            ),
        )
        con.commit()
    finally:
        con.close()


def _seed(db_path) -> dict[str, int]:
    """按 due_at 各造一篇:未到期 / 窗口内 x2 / 积压 x2 / 已采 on_time / 已采 late。

    R58 之后白名单校验前置到入队之前(``sync_queue(gate_hosts=True)`` 是默认),所以这里
    用真实白名单域(``www.toutiao.com``)造数 —— 原先用 ``example.com`` 的样本在新口径下
    根本不会入队,测的就不是队列了。
    """
    def ok(tag: str) -> str:
        return f"https://www.toutiao.com/article/710000000000000000{tag}/"

    ids = {
        # 窗口内:published -90h ⇒ due -18h, window_end +6h;published -75h ⇒ due -3h,end +21h
        "ready_early": _insert_article(db_path, "ready_early", ok("1"), _iso(-90)),
        "ready_late": _insert_article(db_path, "ready_late", ok("2"), _iso(-75)),
        # 未到期:published -1h ⇒ due +71h
        "scheduled": _insert_article(db_path, "scheduled", ok("3"), _iso(-1)),
        # 积压:published -120h ⇒ end -24h;published -200h ⇒ end -104h
        "backlog_recent": _insert_article(db_path, "backlog_recent", ok("4"), _iso(-120)),
        "backlog_older": _insert_article(db_path, "backlog_older", ok("5"), _iso(-200)),
        # 已采 on_time:published -120h,窗口 [-48h,-24h],快照 -30h
        "done_on_time": _insert_article(db_path, "done_on_time", ok("6"), _iso(-120)),
        # 已采 late:published -240h,窗口 [-168h,-144h],快照 -100h
        "done_late": _insert_article(db_path, "done_late", ok("7"), _iso(-240)),
    }
    _insert_snapshot(db_path, ids["done_on_time"], _iso(-30), reads=500, likes=20)
    _insert_snapshot(db_path, ids["done_late"], _iso(-100), reads=800, likes=None)
    publication.backfill_articles(CATEGORY, days=SPAN, apply=True, db_path=db_path)
    return ids


def test_sync_queue_states_follow_due_at(tmp_path):
    db = _db(tmp_path)
    ids = _seed(db)
    report = metrics_queue.sync_queue(CATEGORY, days=SPAN, db_path=db)
    assert report["window_days"] == SPAN
    assert report["total"] == len(ids)
    assert report["states"] == {
        metrics_queue.STATE_SCHEDULED: 1,
        metrics_queue.STATE_READY: 2,
        metrics_queue.STATE_BACKLOG: 2,
        metrics_queue.STATE_DONE: 2,
        metrics_queue.STATE_EXHAUSTED: 0,
    }


def test_claim_prioritizes_in_window_then_recent_backlog(tmp_path):
    db = _db(tmp_path)
    ids = _seed(db)
    metrics_queue.sync_queue(CATEGORY, days=SPAN, db_path=db)
    batch = metrics_queue.claim_batch(CATEGORY, limit=10, db_path=db)
    assert batch["article_ids"] == [
        ids["ready_early"],   # due -18h,窗口内最早到期
        ids["ready_late"],    # due -3h
        ids["backlog_recent"],  # window_end -24h
        ids["backlog_older"],   # window_end -104h
    ]
    assert batch["eligible"] == 4
    assert batch["gone_skipped"] == 0


def test_gone_ledger_articles_are_not_claimed(tmp_path):
    db = _db(tmp_path)
    ids = _seed(db)
    metrics_queue.sync_queue(CATEGORY, days=SPAN, db_path=db)
    from ttxt.db import connect

    con = connect(db)
    try:
        payload = {
            str(ids["ready_early"]): {
                "status": 404,
                "outcome": metrics_pages.OUTCOME_GONE,
                "url": "https://www.toutiao.com/article/7100000000000000001/",
                "checked_at": _iso(),
            }
        }
        con.execute(
            "INSERT OR REPLACE INTO articles_meta(key, value) VALUES(?, ?)",
            (metrics_pages.LINK_STATUS_KEY, json.dumps(payload)),
        )
        con.commit()
    finally:
        con.close()
    batch = metrics_queue.claim_batch(CATEGORY, limit=10, db_path=db)
    assert ids["ready_early"] not in batch["article_ids"]
    assert batch["gone_skipped"] == 1


def test_retry_backoff_and_exhaustion(tmp_path):
    db = _db(tmp_path)
    ids = _seed(db)
    metrics_queue.sync_queue(CATEGORY, days=SPAN, db_path=db)
    target = ids["ready_early"]
    fail = [{"article_id": target, "outcome": metrics_pages.OUTCOME_FETCH_FAILED, "reason": "网络失败"}]

    first = metrics_queue.record_attempts(CATEGORY, fail, db_path=db)
    assert first["planned"][0]["attempts"] == 1
    assert first["planned"][0]["state"] == metrics_queue.STATE_READY
    assert metrics_queue.claim_batch(CATEGORY, limit=10, db_path=db)["article_ids"][0] != target

    second = metrics_queue.record_attempts(CATEGORY, fail, db_path=db)
    assert second["planned"][0]["attempts"] == 2
    third = metrics_queue.record_attempts(CATEGORY, fail, db_path=db)
    assert third["planned"][0]["attempts"] == 3
    assert third["planned"][0]["state"] == metrics_queue.STATE_EXHAUSTED
    assert target not in metrics_queue.claim_batch(CATEGORY, limit=10, db_path=db)["article_ids"]


def test_gone_outcome_is_terminal(tmp_path):
    db = _db(tmp_path)
    ids = _seed(db)
    metrics_queue.sync_queue(CATEGORY, days=SPAN, db_path=db)
    target = ids["ready_early"]
    result = metrics_queue.record_attempts(
        CATEGORY,
        [{"article_id": target, "outcome": metrics_pages.OUTCOME_GONE, "reason": "404"}],
        db_path=db,
    )
    assert result["planned"][0]["state"] == metrics_queue.STATE_DONE


def _fake_collector(captured_at: str, write: bool = True, respect_host: bool = False):
    def _allowed(db_path, ids):
        if not respect_host:
            return ids
        from ttxt.db import connect

        con = connect(db_path)
        try:
            rows = con.execute(
                f"SELECT id, url, url_normalized FROM articles WHERE id IN ({','.join('?' for _ in ids)})",
                ids,
            ).fetchall()
        finally:
            con.close()
        return [
            int(row["id"])
            for row in rows
            if metrics_pages.host_allowed(str(row["url_normalized"] or row["url"] or ""))
        ]

    def collect(category, db_path=None, limit=None, apply=False, article_ids=None, **kwargs):
        ids = _allowed(db_path, list(article_ids or []))
        if not write or not apply:
            return {
                "candidates": len(ids),
                "fetched": 0,
                "written": 0,
                "outcomes": [],
            }
        rows = [
            {
                "article_id": article_id,
                "reads": 100 + article_id,
                "likes": None,
                "favorites": None,   # 公开页永远采不到收藏:必须保持 NULL
                "comments": None,
                "impressions": None,
                "captured_at": captured_at,
            }
            for article_id in ids
        ]
        written = metrics_import.write_metric_rows(
            category, rows, metrics_pages.SOURCE_ID, db_path=db_path, captured_at=captured_at
        )
        return {
            "candidates": len(ids),
            "fetched": len(ids),
            "written": written.get("written", 0),
            "snapshots": written.get("snapshots", 0),
            "changes": written.get("changes", []),
            "outcomes": [
                {"article_id": article_id, "outcome": metrics_pages.OUTCOME_OK, "reason": None}
                for article_id in ids
            ],
        }

    return collect


def test_run_due_apply_refreshes_windows_and_keeps_favorites_null(tmp_path):
    db = _db(tmp_path)
    ids = _seed(db)
    now_iso = _iso()
    report = metrics_queue.run_due(
        CATEGORY,
        limit=10,
        apply=True,
        days=SPAN,
        now=now_iso,
        db_path=db,
        collector=_fake_collector(now_iso),
    )
    assert report["claimed"] == 4
    assert report["windows_refreshed"] == 4
    after = report["after"]
    assert after["status_counts"][publication.STATUS_ON_TIME] == 3  # 原有 1 + 新采 2
    assert after["status_counts"][publication.STATUS_LATE] == 3     # 原有 1 + 积压 2
    assert after["readings"] == 6  # 原 2 + 新采 4

    from ttxt.db import connect

    con = connect(db)
    try:
        rows = con.execute(
            "SELECT favorites FROM metric_snapshots WHERE article_id=?", (ids["ready_early"],)
        ).fetchall()
        assert rows and all(row["favorites"] is None for row in rows)
        queue_state = con.execute(
            "SELECT state FROM metrics_due_queue WHERE category=? AND article_id=?",
            (CATEGORY, ids["ready_early"]),
        ).fetchone()["state"]
        assert queue_state == metrics_queue.STATE_DONE
    finally:
        con.close()


def test_unsupported_host_is_terminal_not_retried(tmp_path):
    """R58 口径:白名单校验前置 ⇒ 白名单外根本不入队;已排进去的按 unsupported 归档、不重试。"""
    db = _db(tmp_path)
    good = _insert_article(
        db, "good", "https://www.toutiao.com/article/3333333333333333333/", _iso(-90)
    )
    bad = _insert_article(db, "bad", "https://example.com/not-allowed", _iso(-90))
    publication.backfill_articles(CATEGORY, days=SPAN, apply=True, db_path=db)
    sync = metrics_queue.sync_queue(CATEGORY, days=SPAN, db_path=db)
    assert sync["host_skipped"] == 1  # 不入队、不占每轮额度
    now_iso = _iso()
    report = metrics_queue.run_due(
        CATEGORY,
        limit=10,
        apply=True,
        days=SPAN,
        now=now_iso,
        db_path=db,
        collector=_fake_collector(now_iso, respect_host=True),
    )
    assert report["unsupported_host_skipped"] == 0  # 队列里已经没有它,无需再"跳过"
    assert report["collection"]["candidates"] == 1
    assert report["windows_refreshed"] == 1
    from ttxt.db import connect

    con = connect(db)
    try:
        rows = {
            int(row["article_id"]): (row["state"], row["attempts"])
            for row in con.execute(
                "SELECT article_id, state, attempts FROM metrics_due_queue WHERE category=?",
                (CATEGORY,),
            ).fetchall()
        }
    finally:
        con.close()
    assert rows[good] == (metrics_queue.STATE_DONE, 1)
    assert bad not in rows  # 新文章压根没进队列


def test_host_gate_skips_window_rows_and_archives_already_queued(tmp_path):
    """窗口行都不给白名单外文章(前置),旧口径遗留的排队条目按 unsupported 归档不重试。"""
    db = _db(tmp_path)
    bad = _insert_article(db, "bad", "https://example.com/legacy", _iso(-90))
    # 旧口径遗留:已经排进队列、还在 ready
    publication.backfill_articles(CATEGORY, days=SPAN, apply=True, db_path=db)
    metrics_queue.sync_queue(CATEGORY, days=SPAN, db_path=db, gate_hosts=False)
    from ttxt.db import connect

    con = connect(db)
    try:
        state = con.execute(
            "SELECT state FROM metrics_due_queue WHERE category=? AND article_id=?",
            (CATEGORY, bad),
        ).fetchone()["state"]
    finally:
        con.close()
    assert state == metrics_queue.STATE_READY

    archived = metrics_queue.archive_unsupported_hosts(CATEGORY, db_path=db)
    assert archived["total"] == 1
    con = connect(db)
    try:
        row = con.execute(
            "SELECT state, attempts, next_attempt_at, last_outcome FROM metrics_due_queue"
            " WHERE category=? AND article_id=?",
            (CATEGORY, bad),
        ).fetchone()
        # 再来一轮也不该被 claim 到(不重试)
        claimed = metrics_queue.claim_batch(CATEGORY, limit=10, db_path=db)["article_ids"]
        # 新入库的白名单外文章:连窗口行都不补
        _insert_article(db, "new_bad", "https://example.com/new", _iso(-90))
        again = metrics_queue.sync_queue(CATEGORY, days=SPAN, db_path=db, include_new_articles=True)
        windows = int(con.execute(
            "SELECT COUNT(*) FROM performance_windows w JOIN articles a"
            " ON a.id = CAST(w.subject_key AS INTEGER)"
            " WHERE a.url LIKE 'https://example.com/%'"
        ).fetchone()[0])
    finally:
        con.close()
    assert (row["state"], row["attempts"], row["next_attempt_at"]) == (
        metrics_queue.STATE_EXHAUSTED,
        metrics_queue.MAX_ATTEMPTS,
        None,
    )
    assert row["last_outcome"] == metrics_queue.OUTCOME_UNSUPPORTED_HOST
    assert bad not in claimed
    assert again["window_backfill"]["host_gate_skipped"] == 1
    assert windows == 1  # 只有旧口径遗留那一行,新文章没有窗口行


def test_backfill_then_sync_is_idempotent(tmp_path):
    """接进调度器的前提:跑两次结果一致,不产生重复窗口行 / 重复队列项。"""
    db = _db(tmp_path)
    ids = _seed(db)
    extra = [
        _insert_article(
            db, f"new_{n}", f"https://www.toutiao.com/article/72000000000000000{n}/", _iso(-90)
        )
        for n in range(5)
    ]
    first = metrics_queue.sync_queue(
        CATEGORY, days=SPAN, db_path=db, include_new_articles=True, backfill_limit=200
    )
    from ttxt.db import connect

    def fingerprint() -> list[tuple]:
        con = connect(db)
        try:
            queue_rows = sorted(
                (int(row["article_id"]), row["state"], int(row["attempts"]), row["due_at"])
                for row in con.execute(
                    "SELECT article_id, state, attempts, due_at FROM metrics_due_queue WHERE category=?",
                    (CATEGORY,),
                )
            )
            windows = sorted(
                (str(row["subject_key"]), row["due_at"], row["window_end"], row["status"])
                for row in con.execute(
                    "SELECT subject_key, due_at, window_end, status FROM performance_windows"
                    " WHERE subject_type='article'"
                )
            )
        finally:
            con.close()
        return [queue_rows, windows]

    snap_first = fingerprint()
    assert first["window_backfill"]["windows_inserted"] == len(extra)
    assert set(first["states"]) and first["total"] == len(ids) + len(extra)
    for article_id in extra:
        assert article_id in {row[0] for row in snap_first[0]}

    second = metrics_queue.sync_queue(
        CATEGORY, days=SPAN, db_path=db, include_new_articles=True, backfill_limit=200
    )
    snap_second = fingerprint()
    assert second["window_backfill"]["windows_inserted"] == 0  # 候选集已空
    assert second["window_backfill"]["missing_windows_total"] == 0
    assert snap_second == snap_first  # 逐行一致:没有重复行,状态也没被改坏

    con = connect(db)
    try:
        dup_window = int(con.execute(
            "SELECT COUNT(*) FROM (SELECT subject_key, window_days FROM performance_windows"
            " WHERE subject_type='article' GROUP BY subject_key, window_days HAVING COUNT(*)>1)"
        ).fetchone()[0])
        dup_queue = int(con.execute(
            "SELECT COUNT(*) FROM (SELECT category, article_id FROM metrics_due_queue"
            " WHERE category=? GROUP BY category, article_id HAVING COUNT(*)>1)",
            (CATEGORY,),
        ).fetchone()[0])
    finally:
        con.close()
    assert dup_window == 0 and dup_queue == 0


def test_infra_failure_does_not_burn_attempts_and_pauses_round(tmp_path):
    """代理/网络故障单独分层:不计重试上限、冷却窗口内整轮暂停不开浏览器。"""
    db = _db(tmp_path)
    ids = _seed(db)
    metrics_queue.sync_queue(CATEGORY, days=SPAN, db_path=db)
    target = ids["ready_early"]
    reason = "Page.goto: net::ERR_PROXY_CONNECTION_FAILED at https://www.toutiao.com/article/x/"
    assert metrics_queue.is_infra_failure(reason) is True
    assert metrics_queue.is_infra_failure("HTTP 404 文章已下架") is False

    result = metrics_queue.record_attempts(
        CATEGORY, [{"article_id": target, "outcome": "fetch_failed", "reason": reason}], db_path=db
    )
    assert result["planned"][0]["outcome"] == metrics_queue.OUTCOME_INFRA
    assert result["planned"][0]["attempts"] == 0  # 不计入上限,一次代理抖动不能推到 exhausted

    state = metrics_queue.infra_state(CATEGORY, db_path=db)
    assert state["paused"] is True and state["infra_pending"] == 1

    called = []
    report = metrics_queue.run_due(
        CATEGORY,
        limit=10,
        apply=True,
        days=SPAN,
        db_path=db,
        collector=lambda *a, **k: called.append(1) or {"candidates": 0, "outcomes": []},
    )
    assert report["skipped"] == "infra_paused"
    assert report["claimed"] == 0
    assert not called  # 暂停期间一个请求都不发

    after_cooldown = metrics_queue.infra_state(
        CATEGORY, now=_iso(+metrics_queue.INFRA_COOLDOWN_HOURS + 1), db_path=db
    )
    assert after_cooldown["paused"] is False  # 修好后自动恢复,不用人工改库
    resumed = metrics_queue.run_due(
        CATEGORY,
        limit=10,
        apply=True,
        days=SPAN,
        now=_iso(+metrics_queue.INFRA_COOLDOWN_HOURS + 1),
        db_path=db,
        collector=_fake_collector(_iso(+metrics_queue.INFRA_COOLDOWN_HOURS + 1)),
    )
    assert resumed["skipped"] is None and resumed["claimed"] >= 1


def test_infra_failures_are_reported_separately_in_round_receipt(tmp_path):
    """回执里代理故障与内容级失败分开计,便于报项目负责人决定是否暂停整条链路。"""
    db = _db(tmp_path)
    _seed(db)
    metrics_queue.sync_queue(CATEGORY, days=SPAN, db_path=db)
    picked = metrics_queue.claim_batch(CATEGORY, limit=2, db_path=db)["article_ids"]
    assert len(picked) == 2

    def collector(category, db_path=None, **kwargs):
        return {
            "candidates": 2,
            "fetched": 1,
            "written": 0,
            "outcome_counts": {"fetch_failed": 2},
            "outcomes": [
                {"article_id": picked[0], "outcome": "fetch_failed",
                 "reason": "net::ERR_PROXY_CONNECTION_FAILED"},
                {"article_id": picked[1], "outcome": "http_error", "reason": "HTTP 404"},
            ],
        }

    report = metrics_queue.run_due(
        CATEGORY, limit=10, apply=True, days=SPAN, db_path=db, collector=collector
    )
    assert report["infra_failures"] == 1
    assert report["infra_down"] is True
    assert report["infra"]["paused"] is True

    from ttxt.db import connect

    con = connect(db)
    try:
        proxy_row = con.execute(
            "SELECT state, attempts, last_outcome FROM metrics_due_queue"
            " WHERE category=? AND article_id=?",
            (CATEGORY, picked[0]),
        ).fetchone()
        content_row = con.execute(
            "SELECT state, attempts, last_outcome FROM metrics_due_queue"
            " WHERE category=? AND article_id=?",
            (CATEGORY, picked[1]),
        ).fetchone()
    finally:
        con.close()
    assert (proxy_row["attempts"], proxy_row["last_outcome"]) == (0, metrics_queue.OUTCOME_INFRA)
    assert content_row["attempts"] == 1  # 内容级失败照旧计次
    assert content_row["last_outcome"] == "http_error"


def test_dry_run_does_not_advance_queue(tmp_path):
    db = _db(tmp_path)
    ids = _seed(db)
    report = metrics_queue.run_due(
        CATEGORY,
        limit=10,
        apply=False,
        days=SPAN,
        db_path=db,
        collector=_fake_collector(_iso(), write=False),
    )
    assert report["claimed"] == 4
    assert report["attempts"] is None
    from ttxt.db import connect

    con = connect(db)
    try:
        row = con.execute(
            "SELECT state, attempts FROM metrics_due_queue WHERE category=? AND article_id=?",
            (CATEGORY, ids["ready_early"]),
        ).fetchone()
        assert (row["state"], row["attempts"]) == (metrics_queue.STATE_READY, 0)
    finally:
        con.close()


def test_candidates_with_explicit_ids_keeps_order_and_skips_gone(tmp_path):
    db = _db(tmp_path)
    first = _insert_article(db, "a", "https://www.toutiao.com/article/1111111111111111111/", _iso(-90))
    second = _insert_article(db, "b", "https://www.toutiao.com/article/2222222222222222222/", _iso(-75))
    third = _insert_article(db, "c", "https://example.com/not-allowed", _iso(-90))
    from ttxt.db import connect

    con = connect(db)
    try:
        payload = {
            str(first): {
                "status": 404,
                "outcome": metrics_pages.OUTCOME_GONE,
                "url": "https://www.toutiao.com/article/1111111111111111111/",
                "checked_at": _iso(),
            }
        }
        con.execute(
            "INSERT OR REPLACE INTO articles_meta(key, value) VALUES(?, ?)",
            (metrics_pages.LINK_STATUS_KEY, json.dumps(payload)),
        )
        con.commit()
    finally:
        con.close()
    picked, host_skipped, gone = metrics_pages._candidates(
        CATEGORY, db, None, article_ids=[second, first, third]
    )
    assert [item["article_id"] for item in picked] == [second]
    assert gone == [first]
    assert host_skipped == 1


def test_earliest_window_observation_still_wins(tmp_path):
    db = _db(tmp_path)
    ids = _seed(db)
    article_id = ids["done_on_time"]  # 窗口 [-48h,-24h],已有 -30h
    _insert_snapshot(db, article_id, _iso(-28), reads=999, likes=88)
    from ttxt.db import connect

    con = connect(db)
    try:
        result = publication.refresh_article_window(con, article_id, CATEGORY, days=SPAN)
        con.commit()
    finally:
        con.close()
    assert result["status"] == publication.STATUS_ON_TIME
    assert result["reads"] == 500  # 窗口内最早那条(-30h),不是后采的 999


def test_timing_report_exposes_late_ratio_and_recoverable(tmp_path):
    db = _db(tmp_path)
    _seed(db)
    metrics_queue.sync_queue(CATEGORY, days=SPAN, db_path=db)
    report = metrics_queue.timing_report(CATEGORY, days=SPAN, db_path=db)
    assert report["articles_with_published_at"] == 7
    assert report["readings"] == 2
    assert report["late_ratio"] == 0.5
    assert report["on_time_coverage"] == round(1 / 7, 6)
    assert report["missing_ratio"]["favorites"] == 1.0
    assert report["recoverable_on_time_in_window"] == 2
    assert report["queue"][metrics_queue.STATE_READY] == 2
    assert report["retry_policy"]["max_attempts"] == 3
