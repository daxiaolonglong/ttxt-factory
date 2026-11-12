"""P12 第二批(受限 Worker 真正消费队列)验收测试。

覆盖:
- 旧库幂等迁移补 ``owner``/``lease_expires_at`` 列(不重建、不丢行);
- ``claim_next_crawl_job`` 原子认领:并发只有一个赢、同源不并发、
  只认领 ``job_type='crawl'``、``attempts`` 认领时 +1 且 ``max_attempts`` 真正封顶;
- 崩溃恢复改判租约列:过期 running → failed → requeue;未来租约不误杀;
- ``runner.run_next`` 认领后对不存在来源仍落终态 ``aborted``;
- Worker:单实例锁、停止标志、``max_jobs``/``max_runtime`` 边界退出、
  崩溃后先恢复再消费、Worker 源码不含任何模型/写作/发布导入。

约定:全部 tmp 路径与假来源,绝不触碰生产库、真实网络、Strata 与发布通道。
"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from ttxt import config as cfg
from ttxt import db
from ttxt.crawler import fetcher, robots, runner, store, worker
from ttxt.crawler import sources as source_loader


# ---------------------------------------------------------------- 替身基础设施(与第一批同构)

class FakeRules:
    def __init__(self, disallow: tuple[str, ...] = (), unknown: bool = False):
        self.disallow = disallow
        self.unknown = unknown
        self.crawl_delay_s = None

    def allowed(self, path: str) -> bool:
        if self.unknown:
            return False
        return not any(path.startswith(p) for p in self.disallow)


class FakeCss:
    def __init__(self, items):
        self._items = list(items)

    def __len__(self):
        return len(self._items)

    def __getitem__(self, i):
        return self._items[i]

    def get(self):
        return self._items[0] if self._items else None

    def getall(self):
        return list(self._items)


class FakeElement:
    def __init__(self, text="", attrib=None):
        self.text = text
        self.attrib = attrib or {}


class FakePage:
    def __init__(self, mapping: dict[str, object]):
        self._mapping = mapping

    def css(self, selector: str):
        return FakeCss(self._mapping.get(selector, []))


class FetchRecorder:
    def __init__(self, pages: dict[str, FakePage]):
        self.pages = pages
        self.calls: list[dict] = []

    def __call__(self, url, timeout=None, render=False, session=None):
        self.calls.append({"url": url, "render": render, "session": session})
        return SimpleNamespace(page=self.pages[url], status=200, url=url, elapsed_s=0.0)


def quote_page(quotes=("第一块引文内容甲", "第二块引文内容乙")) -> FakePage:
    return FakePage({".quote": [FakeElement(text=q) for q in quotes]})


@pytest.fixture
def system_db(tmp_path, monkeypatch):
    path = tmp_path / "system.db"
    db.init_system_db(path)
    monkeypatch.setattr(store, "system_db_path", lambda: path)
    return path


@pytest.fixture
def control_dir(tmp_path, monkeypatch):
    """把 worker 的锁/停止标志隔离到 tmp,绝不碰 00_control。"""
    ctl = tmp_path / "control"
    ctl.mkdir()
    monkeypatch.setattr(cfg, "CONTROL_DIR", ctl)
    return ctl


@pytest.fixture
def fake_insert(monkeypatch):
    def _fake(category, source_id, items, *args, **kwargs):
        return {"inserted": len(items), "duplicates": 0}

    monkeypatch.setattr(store, "insert_items", _fake)


def job_row(db_path: Path, job_id: int) -> sqlite3.Row:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return con.execute("SELECT * FROM crawl_jobs WHERE id=?", (job_id,)).fetchone()
    finally:
        con.close()


def insert_raw_job(db_path: Path, **cols) -> int:
    """按显式列插入(用于 max_attempts、running、租约等 create 覆盖不了的场景)。"""
    cols.setdefault("job_type", "crawl")
    cols.setdefault("status", "queued")
    cols.setdefault("attempts", 0)
    cols.setdefault("max_attempts", 3)
    cols.setdefault("created_at", cfg.now_iso())
    names = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    con = sqlite3.connect(db_path)
    try:
        cur = con.execute(f"INSERT INTO crawl_jobs({names}) VALUES({marks})", tuple(cols.values()))
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def write_sources(tmp_path: Path, monkeypatch, entries: list[dict]) -> None:
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"sources": entries}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(source_loader, "SOURCES_PATH", path)


def html_source_entry(**overrides) -> dict:
    entry = {
        "id": "fake_html",
        "name": "fake html",
        "kind": "html",
        "category": "demo",
        "url": "https://demo.example.com/page1/",
        "enabled": True,
        "render": "http",
        "rate_limit_s": 0.0,
        "max_pages": 5,
        "item_selector": ".quote",
        "title_selector": None,
        "author_selector": None,
        "max_articles": 20,
        "demo": True,
    }
    entry.update(overrides)
    return entry


# ---------------------------------------------------------------- 迁移:旧库补租约列

def test_migration_adds_lease_columns_without_losing_rows(tmp_path):
    path = tmp_path / "old_system.db"
    old_schema = re.sub(r"\n  (owner|lease_expires_at) TEXT,", "\n", db.SYSTEM_SCHEMA)
    assert "owner" not in old_schema  # 确认真的构造出了旧版 crawl_jobs
    con = sqlite3.connect(path)
    con.executescript(old_schema)
    con.execute(
        "INSERT INTO crawl_jobs(job_type, category, source, status, created_at) "
        "VALUES('crawl','demo','legacy_src','queued','2026-11-01T00:00:00+08:00')"
    )
    con.commit()
    con.close()

    db.init_system_db(path)  # 幂等迁移:CREATE IF NOT EXISTS + ALTER 补列

    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(crawl_jobs)")}
        assert {"owner", "lease_expires_at"} <= cols
        row = con.execute("SELECT * FROM crawl_jobs WHERE source='legacy_src'").fetchone()
        assert row is not None and row["status"] == "queued"  # 旧行原样保留
    finally:
        con.close()
    # 再跑一遍仍幂等
    db.init_system_db(path)


# ---------------------------------------------------------------- 原子认领

def test_claim_concurrent_only_one_winner(system_db):
    job_id = store.create_crawl_job("src_a", "demo", db_path=system_db)
    barrier = threading.Barrier(2)
    outcomes: list[dict | None] = []
    lock = threading.Lock()

    def _grab(name: str) -> None:
        barrier.wait()
        got = store.claim_next_crawl_job(name, db_path=system_db)
        with lock:
            outcomes.append(got)

    threads = [threading.Thread(target=_grab, args=(f"w{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [o for o in outcomes if o is not None]
    assert len(winners) == 1  # 同一个任务不可能被两个执行者拿到
    assert winners[0]["id"] == job_id
    row = job_row(system_db, job_id)
    assert row["status"] == "running"
    assert row["attempts"] == 1  # 认领即计一次尝试
    assert row["owner"] in {"w0", "w1"}
    assert row["lease_expires_at"]  # 租约已写入


def test_claim_blocks_same_source_allows_other_source(system_db):
    a1 = insert_raw_job(system_db, source="src_a", category="demo")
    a2 = insert_raw_job(system_db, source="src_a", category="demo")
    b1 = insert_raw_job(system_db, source="src_b", category="demo")

    first = store.claim_next_crawl_job("w", db_path=system_db)
    assert first and first["id"] == a1
    second = store.claim_next_crawl_job("w", db_path=system_db)
    assert second and second["id"] == b1  # 同 src_a 的 a2 被挡,放行 src_b
    third = store.claim_next_crawl_job("w", db_path=system_db)
    assert third is None  # 两个 running 未清,a2 仍不可认领
    assert job_row(system_db, a2)["status"] == "queued"


def test_claim_ignores_non_crawl_job_type(system_db):
    job_id = insert_raw_job(system_db, job_type="model", source="model_src", category="demo")
    assert store.claim_next_crawl_job("w", db_path=system_db) is None
    assert job_row(system_db, job_id)["status"] == "queued"


def test_claim_respects_category_filter(system_db):
    insert_raw_job(system_db, source="src_a", category="tech")
    assert store.claim_next_crawl_job("w", category="demo", db_path=system_db) is None
    got = store.claim_next_crawl_job("w", category="tech", db_path=system_db)
    assert got and got["category"] == "tech"


def test_attempts_and_max_attempts_truly_cap_retries(system_db):
    job_id = insert_raw_job(system_db, source="src_a", category="demo", max_attempts=2)
    for expected_attempts in (1, 2):
        got = store.claim_next_crawl_job("w", db_path=system_db)
        assert got and got["attempts"] == expected_attempts
        outcome = store.fail_crawl_job(job_id, "网络失败", db_path=system_db)
        expected_status = "queued" if expected_attempts < 2 else "failed"
        assert outcome == expected_status
    assert store.claim_next_crawl_job("w", db_path=system_db) is None
    row = job_row(system_db, job_id)
    assert row["status"] == "failed" and row["attempts"] == 2


# ---------------------------------------------------------------- 崩溃恢复(租约驱动)

def test_recover_uses_lease_expiry_not_wall_clock_guess(system_db):
    now = datetime.now().astimezone()
    past = (now - timedelta(hours=2)).isoformat(timespec="seconds")
    future = (now + timedelta(hours=1)).isoformat(timespec="seconds")
    long_ago = (now - timedelta(hours=5)).isoformat(timespec="seconds")

    expired = insert_raw_job(system_db, source="src_a", category="demo", status="running",
                             attempts=1, started_at=long_ago, lease_expires_at=past, owner="dead-w")
    alive = insert_raw_job(system_db, source="src_b", category="demo", status="running",
                           attempts=1, started_at=long_ago, lease_expires_at=future, owner="live-w")
    legacy = insert_raw_job(system_db, source="src_c", category="demo", status="running",
                            attempts=1, started_at=long_ago)  # 旧行无租约列值 → 回退 started_at 判断

    recovered = store.recover_stale_crawl_jobs(lease_s=1800.0, db_path=system_db)
    assert recovered == 2
    assert job_row(system_db, expired)["status"] == "failed"
    assert "dead-w" in (job_row(system_db, expired)["last_error"] or "")
    assert job_row(system_db, alive)["status"] == "running"  # 未过期不误杀
    assert job_row(system_db, legacy)["status"] == "failed"

    requeued = store.requeue_failed_crawl_jobs(db_path=system_db)
    assert requeued == 2
    assert job_row(system_db, expired)["status"] == "queued"  # 断点续采:重新排队


def test_worker_startup_recovers_killed_previous_run(system_db, control_dir):
    now = datetime.now().astimezone()
    insert_raw_job(
        system_db, source="src_a", category="demo", status="running", attempts=1,
        started_at=(now - timedelta(hours=3)).isoformat(timespec="seconds"),
        lease_expires_at=(now - timedelta(hours=2)).isoformat(timespec="seconds"),
        owner="killed-w",
    )
    summary = worker.run_worker("fresh-w", max_jobs=0, max_runtime_s=0.0, idle_stop_s=0.0)
    assert summary["recovered"] == 1
    assert summary["requeued"] == 1
    assert summary["stop_reason"] == "max_runtime"


# ---------------------------------------------------------------- run_next 语义

def test_run_next_aborts_missing_source_after_claim(system_db, tmp_path, monkeypatch):
    write_sources(tmp_path, monkeypatch, [])  # 白名单里没有任何来源
    job_id = store.create_crawl_job("ghost_src", "demo", db_path=system_db)
    result = runner.run_next(owner="manual")
    assert result and result["error"] == "source_missing"
    row = job_row(system_db, job_id)
    assert row["status"] == "aborted"
    assert row["attempts"] == 1 and row["owner"] == "manual"


def test_run_queued_reuses_claim_channel(system_db, tmp_path, monkeypatch, fake_insert):
    write_sources(tmp_path, monkeypatch, [html_source_entry()])
    pages = {"https://demo.example.com/page1/": quote_page()}
    monkeypatch.setattr(fetcher, "fetch_page", FetchRecorder(pages))
    monkeypatch.setattr(robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules())
    monkeypatch.setattr(store, "init_articles_db", lambda category, db_path=None: None)
    job_id = store.create_crawl_job("fake_html", "demo", db_path=system_db)
    results = runner.run_queued(limit=5)
    assert len(results) == 1 and results[0]["ok"] is True
    row = job_row(system_db, job_id)
    assert row["status"] == "done"
    assert row["owner"] == "manual-cli" and row["lease_expires_at"]


# ---------------------------------------------------------------- Worker 边界

def test_worker_second_instance_refused_by_lock(system_db, control_dir):
    held = worker._acquire_lock()
    assert held is not None
    with pytest.raises(worker.WorkerAlreadyRunning):
        worker.run_worker("second", max_runtime_s=0.0)
    worker._release_lock(held)
    # 锁释放后允许重新启动(OS 自动回收,强杀场景不需要人工删锁文件)
    summary = worker.run_worker("restarted", max_runtime_s=0.0, idle_stop_s=0.0)
    assert summary["owner"] == "restarted"


def test_worker_stop_file_leaves_queue_untouched(system_db, control_dir):
    job_id = store.create_crawl_job("fake_html", "demo", db_path=system_db)
    stop = worker.request_stop()
    assert stop.exists()
    summary = worker.run_worker("w", max_jobs=5, idle_stop_s=0.0)
    assert summary["stop_reason"] == "stop_file"
    assert summary["done"] == 0
    assert job_row(system_db, job_id)["status"] == "queued"  # 停止不是吞任务


def test_worker_processes_jobs_and_honors_max_jobs(system_db, control_dir, tmp_path, monkeypatch, fake_insert):
    write_sources(tmp_path, monkeypatch, [html_source_entry()])
    pages = {"https://demo.example.com/page1/": quote_page()}
    monkeypatch.setattr(fetcher, "fetch_page", FetchRecorder(pages))
    monkeypatch.setattr(robots, "fetch_rules", lambda url, ua, timeout=10.0: FakeRules())
    monkeypatch.setattr(store, "init_articles_db", lambda category, db_path=None: None)
    ids = [store.create_crawl_job("fake_html", "demo", db_path=system_db) for _ in range(2)]

    summary = worker.run_worker("test-worker", max_jobs=1, idle_stop_s=0.0)
    assert summary["done"] == 1 and summary["stop_reason"] == "max_jobs"
    statuses = [job_row(system_db, j)["status"] for j in ids]
    assert statuses[0] == "done" and statuses[1] == "queued"
    assert job_row(system_db, ids[0])["owner"] == "test-worker"

    # 停止标志清除后继续消费剩余队列
    summary2 = worker.run_worker("test-worker", max_jobs=5, idle_stop_s=0.5, poll_s=0.01)
    assert summary2["done"] == 1 and summary2["stop_reason"] == "idle"
    assert job_row(system_db, ids[1])["status"] == "done"


def test_worker_module_has_no_model_or_publish_imports():
    """边界用源码结构证明:Worker 不可能触发模型/写作/发布。"""
    tree = ast.parse(Path(worker.__file__).read_text(encoding="utf-8"))
    imported: list[str] = []
    local_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相对导入:ttxt.crawler 包内模块看 alias 名
                local_names.update(alias.name for alias in node.names)
            else:
                imported.append(node.module or "")
    forbidden = ("model", "writing", "publish", "strata")
    offenders = [m for m in imported if any(f in m.lower() for f in forbidden)]
    assert offenders == []
    assert {"runner", "store"} <= local_names  # 确实在用认领/租约通道,而不是绕过它
