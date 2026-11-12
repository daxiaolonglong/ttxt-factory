"""P8 端到端验收脚本。

流程:预检 → 采集 → 清洗 → 分析 → 真实写作 → 面板 → 故障注入 → 备份/恢复 → 磁盘 → 串行校验。
运行:python scripts/e2e.py(需 PYTHONPATH=D:\\ttxt 或从 D:\\ttxt 运行)
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import threading
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backup  # noqa: E402
from ttxt import config as cfg, housekeep  # noqa: E402
from ttxt.analysis import analyze  # noqa: E402
from ttxt.crawler import pipeline, runner, sources, store  # noqa: E402
from ttxt.dashboard import server  # noqa: E402
from ttxt.model_gate import client as model_client  # noqa: E402
from ttxt.writing import workflow  # noqa: E402


def _step(name, fn):
    try:
        result = fn()
        return {"step": name, "ok": True, "result": result}
    except Exception as exc:  # noqa: BLE001 - 汇总失败继续后续
        return {"step": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"}


def preflight():
    report = housekeep.report()
    health = model_client.health() or {}
    checks = {
        "paths_within_project": cfg.paths_within_project(),
        "free_gb": report["free_gb"],
        "within_budget": report["within_budget"],
        "strata_loaded": bool(health.get("loaded")),
        "model": health.get("model"),
    }
    assert checks["paths_within_project"] == [], checks
    assert checks["free_gb"] > 5, checks
    assert checks["within_budget"], checks
    assert checks["strata_loaded"], checks
    return checks


def crawl_step():
    summary = runner.crawl_source("quotes_toscrape", category="demo", limit=5, max_pages=1)
    assert summary["ok"], summary
    return {"job_id": summary["job_id"], "inserted": summary["inserted"], "duplicates": summary["duplicates"]}


def clean_step():
    stats = pipeline.run_pipeline("demo")
    assert stats["articles"] >= 20, stats
    return stats


def analyze_step():
    stats = analyze.run_analysis("demo")
    assert stats["viral"]["tiers"].get("missing_data", 0) >= 20, stats
    assert stats["index"]["indexed"] >= 20, stats
    return stats


def write_step():
    result = workflow.write_article(
        "demo",
        "智能音箱与家庭自动化",
        max_revisions=0,
        max_tokens=900,
    )
    path = Path(result["path"])
    assert path.exists(), result
    text = path.read_text(encoding="utf-8")
    assert "待人工确认" in text, "草稿缺少人工确认声明"
    assert result["evidence"], "缺少证据链"
    assert isinstance(result["passed"], bool)
    assert result["passed"] == (result["status"] == "passed")
    # 核心不变量:截断必拒,拒绝必入 rejected 目录
    if result["truncated"]:
        assert result["passed"] is False, "截断草稿不得通过"
        assert "rejected" in str(path), "截断草稿必须进入 rejected 目录"
    return {
        "draft_id": result["draft_id"],
        "title": result["title"],
        "versions": result["versions"],
        "score": result["score"],
        "status": result["status"],
        "passed": result["passed"],
        "truncated": result["truncated"],
        "evidence": len(result["evidence"]),
        "path": str(path),
        "published": False,
    }


def dashboard_step():
    httpd = server.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    base = f"http://{host}:{port}"

    def get(path):
        with urllib.request.urlopen(base + path, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        overview = get("/api/overview")
        articles = get("/api/category/demo/articles?limit=3")
        logs = get("/api/logs?limit=3")
        assert overview["categories"], overview
        assert articles["total"] >= 20, articles
        assert logs, logs
        return {
            "url": base,
            "categories": len(overview["categories"]),
            "articles_total": articles["total"],
            "logs": len(logs),
            "schema_version": overview["schema_version"],
        }
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def failure_injection_step():
    original = sources.SOURCES_PATH.read_text(encoding="utf-8")
    try:
        payload = json.loads(original)
        payload["sources"].append(
            {
                "id": "e2e_unreachable",
                "name": "E2E 不可达源(127.0.0.1:9)",
                "category": "demo",
                "kind": "rss",
                "url": "http://127.0.0.1:9/feed",
                "enabled": True,
                "rate_limit_s": 0.2,
                "max_pages": 1,
                "demo": True,
            }
        )
        sources.SOURCES_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        summary = runner.crawl_source("e2e_unreachable", category="demo")
        assert summary["ok"] is False, summary
        assert summary["error"], summary
        return {"job_id": summary["job_id"], "error": summary["error"]}
    finally:
        sources.SOURCES_PATH.write_text(original, encoding="utf-8")


def backup_restore_step():
    before = store.count_articles("demo")
    zip_path = backup.snapshot(
        "E2E",
        [cfg.CONTROL_DIR / "system.db", store.articles_db_path("demo")],
    )
    # 恢复演练的临时目录位于项目内(99_temp),不写入系统 %TEMP%
    workdir = cfg.TEMP_DIR / f"e2e-restore-{datetime.now().strftime('%H%M%S')}"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(workdir)
        for db_path in (workdir / "db" / "system.db", workdir / "db" / "demo" / "articles.db"):
            con = sqlite3.connect(db_path)
            try:
                integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                con.close()
            assert integrity == "ok", (str(db_path), integrity)
        restored = sqlite3.connect(workdir / "db" / "demo" / "articles.db")
        try:
            restored_count = restored.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        finally:
            restored.close()
        assert restored_count == before, (restored_count, before)
        return {"zip": str(zip_path), "articles": before, "restored_count": restored_count}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def disk_step():
    report = housekeep.report()
    assert report["free_ok"], report
    assert report["within_budget"], report
    return {
        "project_total_gb": report["project_total_gb"],
        "budget_gb": report["budget_gb"],
        "free_gb": report["free_gb"],
    }


def serial_step():
    con = sqlite3.connect(cfg.CONTROL_DIR / "system.db")
    try:
        rows = con.execute(
            "SELECT id, started_at, finished_at FROM model_jobs WHERE status='done' AND started_at IS NOT NULL AND finished_at IS NOT NULL ORDER BY id"
        ).fetchall()
    finally:
        con.close()
    intervals = [
        (datetime.fromisoformat(started), datetime.fromisoformat(finished)) for _id, started, finished in rows
    ]
    overlaps = sum(1 for first, second in zip(intervals, intervals[1:]) if second[0] < first[1])
    assert overlaps == 0, f"model jobs overlap: {overlaps}"
    return {"jobs_checked": len(intervals), "overlaps": overlaps}


def main() -> int:
    steps = [
        ("preflight", preflight),
        ("crawl", crawl_step),
        ("clean", clean_step),
        ("analyze", analyze_step),
        ("write", write_step),
        ("dashboard", dashboard_step),
        ("failure_injection", failure_injection_step),
        ("backup_restore", backup_restore_step),
        ("disk", disk_step),
        ("serial", serial_step),
    ]
    results = []
    for name, fn in steps:
        outcome = _step(name, fn)
        results.append(outcome)
        print(json.dumps(outcome, ensure_ascii=False), flush=True)

    report = {
        "time": cfg.now_iso(),
        "ok": all(item["ok"] for item in results),
        "steps": results,
    }
    target_dir = cfg.WIKI_DIR / "reports"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"P8-e2e-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(target), "ok": report["ok"]}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
