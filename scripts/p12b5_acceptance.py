"""P12 批次5:长跑/故障验收(全部真实环境执行,无模拟结果)。

覆盖场景(逐个记录真实证据,写入 08_wiki/reports/p12b5-acceptance.json):
  A  重复提交入队去重(生产 api.enqueue_crawl)
  B  缺失来源拒绝
  C  跨类目指标导入拒绝 + 未授权来源拒绝(指标数据为标注夹具,机制真实)
  D  网络故障根因记录(真实网络:不可解析主机 + 真实 404)
  E  robots 拒绝真实执行(ftchinese 已知 Disallow 路径)
  F  8 来源完整 Worker 运行 + 并发读压力(独立读进程),零锁错误
  G  硬杀 Worker → 租约回收 → 重新认领完成(租约到期点提前为加速手段,如实标注)
  H  浏览器崩溃恢复(toutiao browser 任务中强杀浏览器进程,再恢复完成)
  I  temp TTL 与磁盘预算(housekeep 真实运行)
  J  Strata 未受影响(前后 /v1/models 对比 + 场景期间 model_jobs 增量=0)

模型任务不参与任何场景;全程 Strata 只读探测。
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ttxt import config as cfg  # noqa: E402
from ttxt.crawler import fetcher, robots, runner, store  # noqa: E402
from ttxt.dashboard import api  # noqa: E402
from ttxt.analysis import metrics_import  # noqa: E402

REPORT = cfg.WIKI_DIR / "reports" / "p12b5-acceptance.json"
PY = str(cfg.PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")

results: dict[str, dict] = {}


def tmp_dir() -> Path:
    p = cfg.TEMP_DIR / "p12b5"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _now() -> str:
    return time.strftime("%H:%M:%S")


def _model_jobs_count() -> int:
    con = sqlite3.connect(cfg.CONTROL_DIR / "system.db")
    try:
        return int(con.execute("SELECT COUNT(*) FROM model_jobs").fetchone()[0])
    finally:
        con.close()


def _crawl_job(job_id: int) -> dict:
    con = sqlite3.connect(cfg.CONTROL_DIR / "system.db")
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT * FROM crawl_jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        con.close()


def _strata_models() -> dict:
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/v1/models", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {"ok": True, "models": sorted(m.get("id", "?") for m in data.get("data", []))}
    except Exception as exc:  # noqa: BLE002
        return {"ok": False, "error": str(exc)}


def scenario_a_duplicate_enqueue() -> None:
    src = "tmtpost_rss"
    r1 = api.enqueue_crawl(src)
    r2 = api.enqueue_crawl(src)
    # 清理:两个都是排队任务,保留一个执行,另一个在执行后自然消费掉
    results["A_duplicate_enqueue"] = {
        "first": r1,
        "second": r2,
        "deduped": bool(r2.get("deduped")),
        "same_job_id": r1.get("job_id") == r2.get("job_id"),
    }


def scenario_b_missing_source() -> None:
    try:
        api.enqueue_crawl("no_such_source_xyz")
        results["B_missing_source"] = {"rejected": False, "error": "未拒绝!"}
    except ValueError as exc:
        results["B_missing_source"] = {"rejected": True, "message": str(exc)}


def scenario_c_metrics_gates(tmp: Path) -> None:
    # 夹具数据(标注 fixture):只为验证导入门禁机制,不构成任何真实指标
    db_dir = cfg.CATEGORIES_DIR / "technology"
    con = sqlite3.connect(db_dir / "articles.db")
    row = con.execute("SELECT url FROM articles WHERE category='technology' LIMIT 1").fetchone()
    con.close()
    fixture = tmp / "crosscat.csv"
    fixture.write_text(f"url,reads,likes,favorites,comments,impressions\n{row[0]},100,10,5,2,1000\n", encoding="utf-8")
    rep1 = metrics_import.import_metrics("finance", fixture, "manual_verified", authorized={"manual_verified": "verified"})
    fixture2 = tmp / "unauth.csv"
    fixture2.write_text("url,reads\nx,1\n", encoding="utf-8")
    rejected = False
    msg = ""
    try:
        metrics_import.import_metrics("technology", fixture2, "made_up_api", authorized={})
    except PermissionError as exc:
        rejected, msg = True, str(exc)[:120]
    results["C_metrics_gates"] = {
        "cross_category_row_errors": rep1["row_errors"][:2],
        "cross_category_written": rep1["written"],
        "unauthorized_rejected": rejected,
        "unauthorized_message": msg,
        "note": "指标内容为合成夹具,仅测门禁机制",
    }


def scenario_d_network_failure() -> None:
    ua = str(cfg.load_config().get("crawler", {}).get("user_agent", "TouTiaoSmartFactory/0.1"))
    cache: dict = {}
    rules, allowed = robots.gate("http://no-such-host.invalid/x", ua, cache)
    gate_info = {"unknown": bool(rules.unknown), "allowed": bool(allowed)}
    # 探针 1:sspai 对不存在路径返回 200(软 404,站点特性,如实记录)
    soft_status: int | None = None
    soft_err = ""
    try:
        soft_status = fetcher.fetch_page("https://sspai.com/_nonexistent_404_probe_", timeout=15).status
    except Exception as exc:  # noqa: BLE001
        soft_err = f"{type(exc).__name__}: {str(exc)[:150]}"
    # 探针 2:真 404 站点,验证终态状态码立即抛 FetchError(terminal)
    err = ""
    terminal_flag = False
    try:
        fetcher.fetch_page("https://www.ifanr.com/zz-p12b5-not-a-real-post", timeout=15)
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {str(exc)[:150]}"
        terminal_flag = bool(getattr(exc, "terminal", False))
    results["D_network_failure"] = {
        "unresolvable_robots_gate": gate_info,
        "sspai_soft404_probe": {"status": soft_status, "error": soft_err},
        "real_404_fetch_error": err,
        "real_404_terminal": terminal_flag,
        "note": "真实网络调用;失败根因必须被记录而非静默丢弃",
    }


def scenario_e_robots_denial() -> None:
    ua = str(cfg.load_config().get("crawler", {}).get("user_agent", "TouTiaoSmartFactory/0.1"))
    cache: dict = {}
    rules, allowed = robots.gate("https://www.ftchinese.com/interactive", ua, cache)
    results["E_robots_denial"] = {
        "url": "https://www.ftchinese.com/interactive",
        "unknown": bool(rules.unknown),
        "allowed": bool(allowed),
        "denied": (not allowed) and (not rules.unknown),
    }


def scenario_f_full_run_with_readers() -> None:
    ids = [
        "sspai_rss", "ruanyifeng_atom", "eastmoney_rss", "tmtpost_rss",
        "ifanr_feed", "who_cn_news", "toutiao_tech",
    ]
    job_ids = []
    for s in ids:
        r = api.enqueue_crawl(s)
        job_ids.append(r.get("job_id"))
    # 独立并发读进程(面板同款读路径 + 类目库直读);结果逐行落盘,避免管道超时丢数据
    reader_out = tmp_dir() / "reader_result.jsonl"
    reader_code = (
        "import sys, time, json; sys.path.insert(0, r'" + str(ROOT) + "');"
        "from ttxt.dashboard import api; from ttxt import config as cfg; import sqlite3;"
        "out=open(r'" + str(reader_out) + "','w',encoding='utf-8'); ok=0; errs=[]\n"
        "for i in range(60):\n"
        "    try:\n"
        "        api.task_queue(); api.categories(); api.overview()\n"
        "        for cat in ('technology','finance','health'):\n"
        "            api.articles(cat, limit=5)\n"
        "            con=sqlite3.connect(cfg.CATEGORIES_DIR/cat/'articles.db'); con.execute('SELECT COUNT(*) FROM articles').fetchone(); con.close()\n"
        "        ok+=1\n"
        "    except Exception as e:\n"
        "        errs.append(f'{type(e).__name__}: {e}')\n"
        "    out.write(json.dumps({'iter':i,'ok':ok,'errs':errs[:3]},ensure_ascii=False)+'\\n'); out.flush()\n"
        "    time.sleep(0.2)\n"
        "out.write(json.dumps({'done':True,'ok':ok,'err_count':len(errs),'errs':errs[:5]},ensure_ascii=False)+'\\n'); out.close()\n"
    )
    reader = subprocess.Popen([PY, "-c", reader_code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True, cwd=str(ROOT))
    started = _model_jobs_count()
    t0 = time.monotonic()
    res = runner.run_queued(limit=10)
    elapsed = round(time.monotonic() - t0, 1)
    try:
        reader.wait(timeout=150)
        reader_state = "finished"
    except subprocess.TimeoutExpired:
        reader.kill()
        reader_state = "killed_incomplete"
    summary_rows = []
    if reader_out.exists():
        for line in reader_out.read_text(encoding="utf-8").splitlines():
            try:
                summary_rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    after = _model_jobs_count()
    done_rows = [r for r in summary_rows if r.get("done")]
    results["F_full_worker_run"] = {
        "enqueued_job_ids": job_ids,
        "elapsed_s": elapsed,
        "jobs": [
            {k: r.get(k) for k in ("job_id", "source", "ok", "inserted", "duplicates", "error", "robots_allowed", "elapsed_s")}
            for r in res
        ],
        "reader_state": reader_state,
        "reader_iterations_ok": done_rows[0]["ok"] if done_rows else None,
        "reader_errors": done_rows[0]["err_count"] if done_rows else None,
        "reader_error_samples": done_rows[0]["errs"] if done_rows else summary_rows[-1:],
        "model_jobs_delta": after - started,
    }


def _queue_leftovers() -> int:
    """run_next 认领 id 最小的 queued 任务;队列有其它残留时场景会互相窃取,先守卫。"""
    con = sqlite3.connect(cfg.CONTROL_DIR / "system.db")
    n = con.execute("SELECT COUNT(*) FROM crawl_jobs WHERE status IN ('queued','running')").fetchone()[0]
    con.close()
    return int(n)


def scenario_g_hard_kill_lease() -> None:
    left = _queue_leftovers()
    if left:
        results["G_hard_kill_lease"] = {"skipped": True, "queue_leftovers_before": left,
                                        "note": "队列非空,run_next 会窃取他人任务;本轮跳过,清空后重跑"}
        return
    r = api.enqueue_crawl("ifanr_feed")
    job_id = r["job_id"]
    code = (
        "import sys, json; sys.path.insert(0, r'" + str(ROOT) + "');"
        "from ttxt.crawler import runner\n"
        "print(json.dumps(runner.run_next(owner='p12b5-hardkill-test'), default=str, ensure_ascii=False))\n"
    )
    w = subprocess.Popen([PY, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
    # 等它认领(status=running)
    claimed = {}
    for _ in range(60):
        claimed = _crawl_job(job_id)
        if claimed.get("status") == "running":
            break
        time.sleep(0.5)
    owner_seen = claimed.get("owner")
    # 硬杀整个 worker 进程(不给任何清理机会)
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(w.pid)], capture_output=True)
    killed_at = _now()
    time.sleep(1)
    mid = _crawl_job(job_id)
    # 加速租约到期:把 lease_expires_at 提前到过去(时钟加速手段,如实标注,不影响判定)
    con = sqlite3.connect(cfg.CONTROL_DIR / "system.db")
    con.execute("UPDATE crawl_jobs SET lease_expires_at=strftime('%Y-%m-%dT%H:%M:%S+08:00','now','+8 hours','-1 seconds') WHERE id=?", (job_id,))
    con.commit()
    con.close()
    recovered = store.recover_stale_crawl_jobs()
    after_rec = _crawl_job(job_id)
    # 设计语义:recover 只标 failed(写入根因字符串);重新排队由 requeue_failed_crawl_jobs(attempts 未达上限)
    requeued = store.requeue_failed_crawl_jobs()
    after_req = _crawl_job(job_id)
    # 第二个执行者重新认领并完成
    done = runner.run_next(owner="p12b5-hardkill-recoverer")
    final = _crawl_job(job_id)
    results["G_hard_kill_lease"] = {
        "job_id": job_id,
        "killed_worker_pid": w.pid,
        "killed_at": killed_at,
        "owner_before_kill": owner_seen,
        "status_after_kill": mid.get("status"),
        "lease_expired_forced": True,
        "recovered_jobs": recovered,
        "status_after_recover": after_rec.get("status"),
        "recover_last_error": (after_rec.get("last_error") or "")[:160],
        "requeued_jobs": requeued,
        "status_after_requeue": after_req.get("status"),
        "attempts_after_recover": after_rec.get("attempts"),
        "rerun_summary_ok": bool(done and done.get("ok")) if isinstance(done, dict) else False,
        "rerun_claimed_job_id": done.get("job_id") if isinstance(done, dict) else None,
        "reclaimed_by": done.get("job_id") if done else None,
        "final_status": final.get("status"),
        "final_attempts": final.get("attempts"),
        "final_owner": final.get("owner"),
    }


def scenario_h_browser_crash() -> None:
    left = _queue_leftovers()
    if left:
        results["H_browser_crash_recovery"] = {"skipped": True, "queue_leftovers_before": left,
                                               "note": "队列非空,run_next 会窃取他人任务;本轮跳过,清空后重跑"}
        return
    r = api.enqueue_crawl("toutiao_tech")
    job_id = r["job_id"]
    # 关键设计:强杀线程在 worker 进程内部触发。本轮为验证"文章阶段全损"分支:
    # 用 logging.Handler 监视采集日志,捕获到列表页 200(channel=tech)后再等 3s 强杀,
    # 必落在第一篇文章抓取中途(文章单篇 ~5-9s)→ 0 篇完成、全篇报错 → 触发全损重试通道。
    # 只按 Name+CommandLine(含 02_crawler)匹配采集器自己的浏览器,绝不误伤用户浏览器。
    post_kill_s = 3
    code = (
        "import sys, json, threading, subprocess, time, logging; sys.path.insert(0, r'" + str(ROOT) + "');\n"
        "from ttxt.crawler import runner\n"
        "killed=[]\n"
        "listing_done = threading.Event()\n"
        "class WatchH(logging.Handler):\n"
        "    def emit(self, rec):\n"
        "        try:\n"
        "            m = rec.getMessage()\n"
        "        except Exception:\n"
        "            return\n"
        "        if 'Fetched (200)' in str(m) and 'channel=tech' in str(m):\n"
        "            listing_done.set()\n"
        "_w = WatchH()\n"
        "for _n in ('', 'scrapling'):\n"
        "    _lg = logging.getLogger(_n); _lg.addHandler(_w); _lg.setLevel(logging.INFO)\n"
        "def killer():\n"
        "    listing_done.wait(timeout=45)\n"
        "    time.sleep(" + str(post_kill_s) + ")\n"
        "    ps = \"Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'chrome|chromium|firefox|camoufox|headless|msedge' -and $_.CommandLine -match '02_crawler' } | ForEach-Object { $_.ProcessId }\"\n"
        "    out = subprocess.run(['powershell','-NoProfile','-Command',ps], capture_output=True, text=True, timeout=30).stdout.split()\n"
        "    for pid in out:\n"
        "        if pid.isdigit():\n"
        "            subprocess.run(['taskkill','/T','/F','/PID',pid], capture_output=True)\n"
        "            killed.append(int(pid))\n"
        "    print(json.dumps({'browser_kill_at': time.strftime('%H:%M:%S'), 'kill_pids': killed, 'listing_was_done': listing_done.is_set()}), flush=True)\n"
        "threading.Thread(target=killer, daemon=True).start()\n"
        "print(json.dumps(runner.run_next(owner='p12b5-browser-crash'), default=str, ensure_ascii=False))\n"
    )
    # 编码钉死 utf-8:worker 输出含中文错误(全损 RuntimeError),locale=gbk 会炸/乱码(实测 UnicodeDecodeError)
    import os as _os
    w = subprocess.Popen([PY, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace",
                         env={**_os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(ROOT))
    kill_evidence: dict = {}
    summary_line: dict | None = None
    worker_hung = False
    try:
        out, _ = w.communicate(timeout=240)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(w.pid)], capture_output=True)
        out, _ = w.communicate(timeout=10)
        worker_hung = True
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "browser_kill_at" in obj:
                kill_evidence = obj
            else:
                summary_line = obj
    crashed = _crawl_job(job_id)
    # 恢复:按崩溃后状态分支处理
    status_now = crashed.get("status")
    recovery = None
    if status_now == "queued":
        recovery = runner.run_next(owner="p12b5-browser-recover")
    elif status_now == "failed":
        store.requeue_failed_crawl_jobs()
        recovery = runner.run_next(owner="p12b5-browser-recover")
    else:  # running:worker 挂死后强杀,租约过期 → 回收重跑
        con = sqlite3.connect(cfg.CONTROL_DIR / "system.db")
        con.execute("UPDATE crawl_jobs SET lease_expires_at=strftime('%Y-%m-%dT%H:%M:%S+08:00','now','+8 hours','-1 seconds') WHERE id=?", (job_id,))
        con.commit()
        con.close()
        store.recover_stale_crawl_jobs()
        store.requeue_failed_crawl_jobs()
        recovery = runner.run_next(owner="p12b5-browser-recover")
    recovered_claim = (recovery or {}).get("job_id") if isinstance(recovery, dict) else None
    final = _crawl_job(job_id)
    results["H_browser_crash_recovery"] = {
        "job_id": job_id,
        "kill_design": f"worker 内部线程:日志监视捕获列表页 200(channel=tech)后再等 {post_kill_s}s 强杀(必落第一篇文章抓取中途)",
        "kill_post_detection_s": post_kill_s,
        "kill_listing_was_done": kill_evidence.get("listing_was_done"),
        "browser_kill_at": kill_evidence.get("browser_kill_at"),
        "kill_pids": kill_evidence.get("kill_pids"),
        "kill_pid_count": len(kill_evidence.get("kill_pids") or []),
        "worker_hung_then_killed": worker_hung,
        "worker_summary": summary_line,
        "status_after_crash": status_now,
        "attempts_after_crash": crashed.get("attempts"),
        "last_error_after_crash": (crashed.get("last_error") or "")[:200],
        "recovered_claimed_job_id": recovered_claim,
        "recovery_result_ok": bool(recovery and recovery.get("ok")) if isinstance(recovery, dict) else False,
        "recovery_raw": str(recovery)[:200] if recovery else None,
        "final_status": final.get("status"),
        "final_attempts": final.get("attempts"),
        "final_inserted_or_dup": {"inserted": (json.loads(final.get("meta_json") or "{}") or {}).get("inserted"),
                                   "duplicates": (json.loads(final.get("meta_json") or "{}") or {}).get("duplicates")},
    }


def scenario_i_housekeep_disk() -> None:
    import shutil

    def du(path: Path) -> int:
        total = 0
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
        return total

    temp_before = du(cfg.TEMP_DIR) if cfg.TEMP_DIR.exists() else 0
    art = cfg.CRAWLER_DIR / ".browser" / "pw-artifacts"
    art_before = len(list(art.glob("*"))) if art.exists() else 0
    proc = subprocess.run([PY, "-m", "ttxt", "housekeep"], capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    temp_after = du(cfg.TEMP_DIR) if cfg.TEMP_DIR.exists() else 0
    art_after = len(list(art.glob("*"))) if art.exists() else 0
    total = du(cfg.PROJECT_ROOT)
    results["I_housekeep_disk"] = {
        "housekeep_exit": proc.returncode,
        "housekeep_tail": proc.stdout.strip().splitlines()[-3:] if proc.stdout else [],
        "temp_bytes_before": temp_before,
        "temp_bytes_after": temp_after,
        "pw_artifacts_dirs_before": art_before,
        "pw_artifacts_dirs_after": art_after,
        "project_total_gb": round(total / 1e9, 3),
        "budget_gb": 20,
        "within_budget": total < 20e9,
    }


def main() -> int:
    only = set()
    if len(sys.argv) > 1 and sys.argv[1] == "--only":
        only = {a.strip().upper() for a in sys.argv[2].split(",")}
    tmp = tmp_dir()
    strata_before = _strata_models()
    jobs_before = _model_jobs_count()
    t0 = time.monotonic()
    scenarios = [
        ("A", scenario_a_duplicate_enqueue, ()),
        ("B", scenario_b_missing_source, ()),
        ("C", scenario_c_metrics_gates, (tmp,)),
        ("D", scenario_d_network_failure, ()),
        ("E", scenario_e_robots_denial, ()),
        ("F", scenario_f_full_run_with_readers, ()),
        ("G", scenario_g_hard_kill_lease, ()),
        ("H", scenario_h_browser_crash, ()),
        ("I", scenario_i_housekeep_disk, ()),
    ]
    for key, fn, args in scenarios:
        if only and key not in only:
            continue
        try:
            fn(*args)
        except Exception as exc:  # noqa: BLE001 - 单场景失败不丢整体证据
            results[f"{key}_FAILED"] = {
                "scenario": fn.__name__,
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            }
    strata_after = _strata_models()
    jobs_after = _model_jobs_count()
    evidence = {
        "generated_at": cfg.now_iso(),
        "wall_clock_s": round(time.monotonic() - t0, 1),
        "strata_before": strata_before,
        "strata_after": strata_after,
        "strata_unchanged": strata_before == strata_after,
        "model_jobs_delta_all_scenarios": jobs_after - jobs_before,
        "scenarios": results,
    }
    if REPORT.exists():  # --only 增量运行时合并旧证据(不覆盖未重跑场景)
        try:
            old = json.loads(REPORT.read_text(encoding="utf-8"))
            merged = old.get("scenarios", {})
            merged.update(results)
            # 陈旧调试键(如旧版 _H_debug)不再由当前场景写入,合并时清除,避免误导
            merged = {k: v for k, v in merged.items() if not k.startswith("_")}
            evidence["scenarios"] = merged
            evidence["previous_generated_at"] = old.get("generated_at")
        except (json.JSONDecodeError, OSError):
            pass
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"evidence written: {REPORT}")
    for name, body in results.items():
        print(name, "=>", json.dumps(body, ensure_ascii=False)[:260])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
