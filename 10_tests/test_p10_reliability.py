"""P10 可靠性测试:类目路由、模型锁、robots 安全、任务恢复、指标缺失≠0。"""

import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from ttxt import db as system_db
from ttxt import housekeep
from ttxt.analysis import viral
from ttxt.crawler import robots, runner, sources, store
from ttxt.model_gate import gate, queue


def _tmp_system(tmp_path):
    return system_db.init_system_db(tmp_path / "system.db")


# ---- F2 类目路由 ----


def test_demo_source_defaults_to_demo():
    assert runner.resolve_category(sources.get_source("quotes_toscrape"), None) == "demo"


def test_demo_source_rejects_other_category():
    with pytest.raises(ValueError):
        runner.resolve_category(sources.get_source("quotes_toscrape"), "technology")


def test_formal_source_defaults_to_own_category():
    assert runner.resolve_category(sources.get_source("toutiao_tech"), None) == "technology"


def test_formal_source_rejects_mismatch():
    with pytest.raises(ValueError):
        runner.resolve_category(sources.get_source("toutiao_tech"), "finance")


# ---- F4 robots ----


def test_robots_wildcard_and_anchor():
    rules = robots.parse_robots(
        "User-agent: *\nDisallow: /a/*.pdf\nDisallow: /exact$\nAllow: /a/ok.pdf\n"
    )
    assert rules.allowed("/a/file.pdf") is False
    assert rules.allowed("/a/ok.pdf") is True
    assert rules.allowed("/exact") is False
    assert rules.allowed("/exact2") is True
    assert rules.allowed("/other") is True


def _start_http(status_code: int) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(status_code)
            self.end_headers()
            self.wfile.write(b"")

        def log_message(self, *_args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def test_robots_404_allows():
    httpd = _start_http(404)
    try:
        rules = robots.fetch_robots(f"http://127.0.0.1:{httpd.server_address[1]}/page", "test/1.0")
        assert rules.unknown is False
        assert rules.allow_all is True
    finally:
        httpd.shutdown()


def test_robots_500_is_unknown():
    httpd = _start_http(500)
    try:
        rules = robots.fetch_robots(f"http://127.0.0.1:{httpd.server_address[1]}/page", "test/1.0")
        assert rules.unknown is True
        assert rules.allowed("/x") is False
    finally:
        httpd.shutdown()


def test_robots_connection_error_is_unknown():
    rules = robots.fetch_robots("http://127.0.0.1:9/page", "test/1.0", timeout=2)
    assert rules.unknown is True


# ---- F3 模型锁 ----


def test_lock_does_not_remove_live_process(tmp_path):
    lock_path = tmp_path / "gate.lock"
    lock = gate.FileLock(lock_path, timeout_s=1.0, stale_s=0.0)
    lock.acquire()
    old = time.time() - 99999
    os.utime(lock_path, (old, old))
    with pytest.raises(TimeoutError):
        gate.FileLock(lock_path, timeout_s=0.4, stale_s=0.0).acquire()
    assert lock_path.exists()
    lock.release()
    assert not lock_path.exists()


def test_lock_reclaims_dead_process(tmp_path):
    lock_path = tmp_path / "gate.lock"
    lock_path.write_text("99999999 0", encoding="ascii")
    old = time.time() - 99999
    os.utime(lock_path, (old, old))
    lock = gate.FileLock(lock_path, timeout_s=2.0, stale_s=1.0)
    lock.acquire()
    assert lock_path.exists()
    lock.release()
    assert not lock_path.exists()


def test_release_checks_ownership(tmp_path):
    lock_path = tmp_path / "gate.lock"
    lock_path.write_text("12345 0", encoding="ascii")
    gate.FileLock(lock_path).release()
    assert lock_path.exists()  # 他人持有,不删除
    lock_path.write_text(f"{os.getpid()} 0", encoding="ascii")
    gate.FileLock(lock_path).release()
    assert not lock_path.exists()


def test_requeue_stale_running(tmp_path):
    db_path = _tmp_system(tmp_path)
    job_id = queue.enqueue("chat", {"prompt": "x"}, db_path=db_path)
    queue.claim_next(db_path=db_path)
    old = (datetime.now().astimezone() - timedelta(minutes=30)).isoformat(timespec="milliseconds")
    con = sqlite3.connect(db_path)
    con.execute("UPDATE model_jobs SET started_at=? WHERE id=?", (old, job_id))
    con.commit()
    con.close()
    recovered = queue.requeue_stale_running(lease_s=900, db_path=db_path)
    assert recovered == 1
    record = queue.get(job_id, db_path=db_path)
    assert record["status"] == "queued"


def test_requeue_stale_skips_fresh(tmp_path):
    db_path = _tmp_system(tmp_path)
    job_id = queue.enqueue("chat", {"prompt": "x"}, db_path=db_path)
    queue.claim_next(db_path=db_path)
    assert queue.requeue_stale_running(lease_s=900, db_path=db_path) == 0
    record = queue.get(job_id, db_path=db_path)
    assert record["status"] == "running"


# ---- F5 采集任务重试/恢复 ----


def test_crawl_attempts_increment_and_requeue(tmp_path):
    db_path = _tmp_system(tmp_path)
    job_id = store.create_crawl_job("s", "demo", db_path=db_path)
    store.mark_job_started(job_id, db_path=db_path)
    con = sqlite3.connect(db_path)
    attempts = con.execute("SELECT attempts FROM crawl_jobs WHERE id=?", (job_id,)).fetchone()[0]
    con.close()
    assert attempts == 1
    assert store.fail_crawl_job(job_id, "boom", db_path=db_path) == "queued"


def test_crawl_fail_exhausts_to_failed(tmp_path):
    db_path = _tmp_system(tmp_path)
    job_id = store.create_crawl_job("s", "demo", db_path=db_path)
    outcome = None
    for _ in range(3):
        store.mark_job_started(job_id, db_path=db_path)
        outcome = store.fail_crawl_job(job_id, "boom", db_path=db_path)
    assert outcome == "failed"


def test_requeue_failed_and_recover_stale(tmp_path):
    db_path = _tmp_system(tmp_path)
    stale = store.create_crawl_job("s1", "demo", db_path=db_path)
    store.mark_job_started(stale, db_path=db_path)
    old = (datetime.now().astimezone() - timedelta(hours=2)).isoformat(timespec="milliseconds")
    con = sqlite3.connect(db_path)
    con.execute("UPDATE crawl_jobs SET started_at=? WHERE id=?", (old, stale))
    con.commit()
    con.close()
    assert store.recover_stale_crawl_jobs(lease_s=1800, db_path=db_path) == 1

    failed = store.create_crawl_job("s2", "demo", db_path=db_path)
    con = sqlite3.connect(db_path)
    con.execute("UPDATE crawl_jobs SET status='failed', attempts=1 WHERE id=?", (failed,))
    con.commit()
    con.close()
    # stale 任务被恢复为 failed(attempts=1)后同样符合重排条件 → 共 2 个被重排
    assert store.requeue_failed_crawl_jobs(db_path=db_path) == 2


# ---- F6 指标缺失 ≠ 0 ----


def test_incomplete_metrics_not_treated_as_zero():
    label, tier, score, _reason = viral.score_with_metrics(
        {},
        {"impressions": 10000, "reads": 3000, "likes": None, "favorites": 300, "comments": 50},
    )
    assert tier == "incomplete_data"
    assert score is None
    assert label == "unscored"


def test_complete_metrics_measured():
    label, tier, score, _reason = viral.score_with_metrics(
        {},
        {"impressions": 10000, "reads": 3000, "likes": 200, "favorites": 300, "comments": 50},
    )
    assert tier == "measured"
    assert score is not None


# ---- F7 限额执行 ----


def test_prune_dir_removes_oldest(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "a.bin").write_bytes(b"x" * 1000)
    time.sleep(0.02)
    (cache_dir / "b.bin").write_bytes(b"y" * 1000)
    result = housekeep._prune_dir(cache_dir, 1500)
    assert result["deleted_files"] == 1
    assert housekeep.dir_size(cache_dir) <= 1500
