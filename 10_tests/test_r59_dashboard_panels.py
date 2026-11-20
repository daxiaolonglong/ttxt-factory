"""R59 类目面板:现算读数 / 类目隔离 / confirm 闸门 / 单飞去重 / 只读连接物理不可写。

C-8 与 P7 同一套:面板只在 07_tmp 下的裁剪副本(``isolated_data_root``)上工作。
本文件不出现任何生产数值常量(415/1014/423 这类):面板数字必须现算,
测试也用同样口径从副本库现算对照 —— 对照的是口径一致,不是某天的数据。
"""

import json
import os
import sqlite3
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from ttxt import config as cfg
from ttxt.dashboard import api, server

PANEL_SLUGS = ("technology", "finance", "social")  # 只是 slug 清单,不含任何数值常量


@pytest.fixture()
def dashboard(isolated_data_root: Path):
    """面板夹具:库路径全部指向 tmp 下的生产裁剪副本(同 P7)。"""
    httpd = server.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    yield f"http://{host}:{port}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def _get(url: str) -> tuple[int, str]:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.status, response.read().decode("utf-8")


def _post(base: str, path: str, payload: dict) -> tuple[int, str]:
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "X-TTXT-Task": "1", "Origin": base},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _copy_db(isolated_data_root: Path, slug: str) -> Path:
    return isolated_data_root / "01_categories" / slug / "articles.db"


def _ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def test_metrics_readout_live_and_isolated(dashboard, isolated_data_root: Path):
    """三个不同类目的数字互不相同,且每个数字都等于该类目副本库的现算值。

    这就是"换类目不是换标题"的可证版本:面板读数 == 独立对该库的查询;
    可量指标口径 == 公开指标 host 白名单逐行判定(非头条域不得计入可补采样本)。
    """
    from ttxt.crawler import metrics_pages

    _, body = _get(dashboard + "/api/categories")
    listed = {item["slug"]: int(item["articles"]) for item in json.loads(body)}
    chosen = [slug for slug in PANEL_SLUGS if slug in listed]
    assert len(chosen) >= 3, f"面板类目清单缺类目:{chosen}"

    seen: set[int] = set()
    for slug in chosen:
        status, payload = _get(f"{dashboard}/api/category/{slug}/metrics")
        assert status == 200, f"metrics 读数未就绪:{slug}"
        data = json.loads(payload)
        con = _ro(_copy_db(isolated_data_root, slug))
        try:
            total = int(con.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
            measurable = sum(
                1
                for row in con.execute("SELECT url, url_normalized FROM articles")
                if metrics_pages.host_allowed(str(row["url_normalized"] or row["url"] or ""))
            )
        finally:
            con.close()
        assert data["articles"] == total, f"{slug}:文章数与所属库对不上({data['articles']} != {total})"
        assert data["measurable_articles"] == measurable, (
            f"{slug}:可量指标口径不符 host 白名单({data['measurable_articles']} != {measurable})"
        )
        assert data["measurable_articles"] <= data["articles"]
        assert listed[slug] == total, f"{slug}:/api/categories 与 metrics 现算不一致"
        seen.add(total)
    assert len(seen) == len(chosen), "三个类目数字相同 ⇒ 换标题式的假隔离"


def test_queue_readout_matches_copy(dashboard, isolated_data_root: Path):
    """队列健康度、待补采数、失败归类计数都等于该类目副本库的现算值。"""
    slug = "technology"
    _, payload = _get(f"{dashboard}/api/category/{slug}/metrics")
    data = json.loads(payload)
    con = _ro(_copy_db(isolated_data_root, slug))
    try:
        try:
            queue = {
                str(row["state"]): int(row["n"])
                for row in con.execute(
                    "SELECT state, COUNT(*) AS n FROM metrics_due_queue WHERE category=? GROUP BY state",
                    (slug,),
                )
            }
            outcomes = {
                str(row["outcome"]): int(row["n"])
                for row in con.execute(
                    "SELECT last_outcome AS outcome, COUNT(*) AS n FROM metrics_due_queue"
                    " WHERE category=? AND last_outcome IS NOT NULL AND last_outcome <> '' GROUP BY last_outcome",
                    (slug,),
                )
            }
        except sqlite3.OperationalError:
            queue, outcomes = {}, {}
    finally:
        con.close()
    assert data["queue"] == queue, f"{slug}:队列状态计数与库不符"
    assert data["pending_backfill"] == sum(queue.get(state, 0) for state in ("scheduled", "ready", "backlog"))
    assert data["failure_outcomes"] == outcomes, f"{slug}:失败归类计数与库不符"


def test_new_routes_ready(dashboard):
    """批 A 新增的四组 GET 路由全部可用(返回真实读数,不是 404/500)。"""
    for path in (
        "/api/category/technology/metrics",
        "/api/category/technology/keywords",
        "/api/category/technology/jobs?limit=5",
        "/api/category/technology/writing",
        "/api/category/finance/metrics",
    ):
        status, body = _get(dashboard + path)
        assert status == 200, f"路由未就绪:{path} -> {status} {body[:200]}"


def test_panel_queue_columns_exist_in_real_schema(dashboard, isolated_data_root: Path):
    """面板读数引用的列必须真实存在。

    批 A 实测踩过:``last_reason`` 这个列名写错后被 ``except OperationalError`` 吞掉,
    队列尝试表永远显示"暂无数据"。现在列名错 ⇒ 路由直接 500,同时这里按真实 schema 核对。
    """
    slug = "technology"
    con = _ro(_copy_db(isolated_data_root, slug))
    try:
        cols = {str(row["name"]) for row in con.execute("PRAGMA table_info(metrics_due_queue)")}
    finally:
        con.close()
    assert cols, "裁剪副本缺 metrics_due_queue 表,这条核对失效"
    referenced = (
        "article_id", "state", "attempts", "last_outcome", "last_error",
        "last_attempt_at", "next_attempt_at", "updated_at", "category",
    )
    missing = sorted(set(referenced) - cols)
    assert not missing, f"面板引用了不存在的列:{missing};真实列={sorted(cols)}"
    status, body = _get(f"{dashboard}/api/category/{slug}/jobs?limit=5")
    assert status == 200, f"jobs 路由因列名报错:{status} {body[:200]}"
    rows = json.loads(body)["metrics_attempts"]
    for row in rows:
        assert {"article_id", "state", "attempts", "last_outcome", "last_error"} <= set(row)


def test_frontend_five_tabs_and_write_guards():
    """批 B 前端:五个页签齐全、写操作都有确认与进行中/已在跑提示、全部走接口取数。"""
    web = cfg.DASHBOARD_DIR / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    js = (web / "app.js").read_text(encoding="utf-8")
    for tab in ("tab-overview", "tab-collect", "tab-metrics", "tab-writing", "tab-logs"):
        assert f'id="{tab}"' in html, f"缺页签:{tab}"
    assert 'id="confirm-modal"' in html, "写操作缺确认弹层"
    for token in ("confirmAction", "confirm: state.category", "已在跑", "正在跑", "/api/category/"):
        assert token in js, f"app.js 缺关键实现:{token}"
    for action in ("/api/tasks/metrics-backfill", "/api/tasks/write", "/api/tasks/crawl", "/api/tasks/analyze"):
        assert action in js, f"app.js 缺写操作:{action}"
    assert "confirm: state.category" in js, "补采/写作必须带 confirm(与目标类目一致)"


def test_write_actions_require_confirm(dashboard):
    """补采与写作缺 confirm(或 confirm 与类目不一致)必 400 —— 400 发生在起活之前。"""
    for path, payload in (
        ("/api/tasks/metrics-backfill", {"category": "technology"}),
        ("/api/tasks/metrics-backfill", {"category": "technology", "confirm": "social"}),
        ("/api/tasks/metrics-backfill", {"category": "technology", "confirm": ""}),
        ("/api/tasks/write", {"category": "technology", "topic": "r59 探针"}),
        ("/api/tasks/write", {"category": "technology", "topic": "r59 探针", "confirm": ""}),
    ):
        status, body = _post(dashboard, path, payload)
        assert status == 400, f"{path} 缺 confirm 必须 400,实得 {status}:{body[:200]}"


def test_inflight_task_deduped(dashboard):
    """in-flight 重复下发 ⇒ deduped,绝不另起一次。

    锁持有者写本测试进程的 pid(必然存活),服务端应看到锁在跑:
    补采不进 run_due、写作不起 Popen,只回 deduped;锁文件也不被抢走。
    """
    slug = "technology"
    runs = cfg.TEMP_DIR / "dashboard_runs"
    runs.mkdir(parents=True, exist_ok=True)
    for action in ("metrics-backfill", "write"):
        lock = runs / f"{slug}-{action}.lock"
        lock.write_text(
            json.dumps({"pid": os.getpid(), "started_at": cfg.now_iso(), "category": slug, "action": action}),
            encoding="utf-8",
        )
        try:
            status, body = _post(
                dashboard, f"/api/tasks/{action}", {"category": slug, "confirm": slug, "topic": "r59 探针"}
            )
            assert status in (200, 202), body[:200]
            data = json.loads(body)
            assert data.get("deduped") is True, f"{action} 未去重:{body[:200]}"
            assert data["running"]["pid"] == os.getpid(), f"{action}:未报告在跑的持有者"
            assert lock.exists(), f"{action}:去重路径把在途锁抢走了"
        finally:
            lock.unlink(missing_ok=True)


def test_stale_lock_is_reclaimed_without_killing_real_work(dashboard):
    """陈旧锁(持有进程已死)自动回收,不会把补采/写作永久卡死。"""
    slug = "technology"
    runs = cfg.TEMP_DIR / "dashboard_runs"
    runs.mkdir(parents=True, exist_ok=True)
    lock = runs / f"{slug}-metrics-backfill.lock"
    dead_probe = os.spawnv(os.P_NOWAIT, sys.executable, [sys.executable, "-c", "pass"])
    os.waitpid(dead_probe, 0)  # 确保 pid 已退出
    lock.write_text(json.dumps({"pid": dead_probe, "started_at": cfg.now_iso()}), encoding="utf-8")
    try:
        acquired, holder = api._acquire_run_lock(slug, "metrics-backfill")
        assert acquired is not None, f"陈旧锁未被回收,holder={holder}"
        acquired.unlink(missing_ok=True)
    finally:
        lock.unlink(missing_ok=True)


def test_readonly_uri_physically_denies_writes(isolated_data_root: Path):
    """面板新增读数走 SQLite 只读 URI:写尝试被物理拒绝,不靠代码自觉。"""
    con = api._ro_connect(_copy_db(isolated_data_root, "technology"))
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("UPDATE articles SET title = title")
        with pytest.raises(sqlite3.OperationalError):
            con.execute("INSERT INTO articles_meta(key, value) VALUES('r59.probe', '1')")
    finally:
        con.close()
