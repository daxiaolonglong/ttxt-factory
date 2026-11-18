"""P35:把「公开互动指标采集」接进现有调度器,让互动数据自动回流。

真人 2026-11-17 复核指出的闭环缺口(已核实):调度器只有 crawl_enqueue / analyze /
recover / housekeep 四种任务,``config.metrics.public_pages`` 有来源、白名单域名、
每轮数量与间隔,但**没有任何调度任务调用它** ⇒ 互动数据只能靠手动跑,爆款反馈链
(采集 → 指标 → 重标定 → 关键词评分 → 反哺采集)中间那段是断的。

这一组测试钉住七件事:
1. 复用现有 ``collect_with_browser``,不重写采集器;采集成功后必须走既有
   「重标定爆款标签 + 关键词评分」链路(闭环才算接上);
2. 按类目独立执行:一个类目失败不能带走其它类目;
3. 默认 dry-run,只有配置里显式 ``apply: true`` 才写生产库;
4. 采集前必须让开浏览器: crawl 队列里有 queued/running 任务就**整轮跳过**,
   不发任何请求(调度进程与 Worker 是两个进程,共用同一个浏览器 profile 会互抢);
   非 crawl 类型任务不参与判断(Worker 永不认领它们);
5. 失败/跳过必须把真实状态留在 ``scheduler_state.json``,不能伪装成 ok;
6. 每轮数量上限从 config 读,并且"没有文章可采"时绝不开浏览器;
7. 每轮落一份可核对的证据文件,失败也要落。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ttxt import config as cfg
from ttxt import db as ttxt_db
from ttxt import scheduler
from ttxt.crawler import store


# --------------------------------------------------------------------------- 替身

class Spy:
    """记录调用顺序与参数,替身全部挂在模块属性上(被测代码在函数内 import 模块)。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def record(self, name: str, returns=None):
        def wrapper(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return returns
        return wrapper

    def names(self) -> list[str]:
        return [name for name, _args, _kwargs in self.calls]

    def args_for(self, name: str) -> list[tuple]:
        return [args for name_, args, _ in self.calls if name_ == name]

    def kwargs_for(self, name: str) -> list[dict]:
        return [kwargs for name_, _args, kwargs in self.calls if name_ == name]


def _job(tmp_path: Path, **overrides) -> dict:
    job = {
        "id": "metrics_fetch_6h",
        "kind": "metrics_fetch",
        "interval_min": 360,
        "enabled": True,
        "category": "demo",
        "apply": True,
        "limit": 7,
        "sleep_s": 0.0,
        "wait_for_queue_s": 0.0,
        "system_db": str(_system_db(tmp_path)),
        "evidence_dir": str(tmp_path / "evidence"),
    }
    job.update(overrides)
    return job


def _system_db(tmp_path: Path) -> Path:
    return ttxt_db.init_system_db(tmp_path / "system.db")


def _install_spy(monkeypatch, *, plan=None, collect=None, relabel=None, analyze=None) -> Spy:
    from ttxt.analysis import analyze as analyzer
    from ttxt.analysis import metrics_import
    from ttxt.crawler import metrics_pages

    spy = Spy()
    monkeypatch.setattr(metrics_pages, "round_plan",
                        plan or spy.record("round_plan", returns={"candidates": 3, "picked_ids": [3, 2, 1],
                                                                  "host_skipped": 0, "known_gone_skipped": 0,
                                                                  "known_gone_ids": []}))
    monkeypatch.setattr(metrics_pages, "collect_with_browser",
                        collect or spy.record("collect", returns={"fetched": 3, "written": 3, "updated": 0,
                                                                  "snapshots": 3, "candidates": 3,
                                                                  "known_gone_skipped": 0, "outcomes": [],
                                                                  "outcome_counts": {"metrics_ok": 3},
                                                                  "dry_run": False}))
    monkeypatch.setattr(metrics_import, "relabel_after_import",
                        relabel or spy.record("relabel", returns={"labeled": 3}))
    monkeypatch.setattr(analyzer, "run_analysis", analyze or spy.record("analyze", returns={"keywords": 4}))
    return spy


# --------------------------------------------------------------------------- 一、闭环接上

def test_metrics_job_collects_then_relabels_then_analyzes(tmp_path, monkeypatch):
    """采集成功 → 立刻重标定爆款标签 → 再跑关键词评分:闭环的三段必须按顺序接上。"""
    spy = _install_spy(monkeypatch)
    result = scheduler.run_job(_job(tmp_path))

    assert spy.names() == ["round_plan", "collect", "relabel", "analyze"], \
        f"必须是 计划→采集→重标定→分析,实际:{spy.names()}"
    assert spy.args_for("collect")[0] == ("demo",), "按类目调用现有采集器,不重写采集逻辑"
    kwargs = spy.kwargs_for("collect")[0]
    assert kwargs["apply"] is True and kwargs["limit"] == 7 and kwargs["sleep_s"] == 0.0
    assert kwargs["retry_gone"] is False, "默认不得绕过已确认下架的跳过台账"
    assert result["scheduler_status"] == "ok"
    assert result["categories"]["demo"]["status"] == "ok"
    assert result["categories"]["demo"]["written"] == 3, "写库数量要能直接对账"


def test_metrics_job_is_dry_run_unless_config_says_apply(tmp_path, monkeypatch):
    """配置里没有 apply ⇒ 默认 dry-run,且什么都不写 ⇒ 不得触发重标定/分析(没有东西可同步)。"""
    spy = _install_spy(monkeypatch)
    result = scheduler.run_job(_job(tmp_path, apply=None))
    kwargs = spy.kwargs_for("collect")[0]
    assert kwargs["apply"] is False, "调度任务默认不得写生产库"
    assert "relabel" not in spy.names() and "analyze" not in spy.names()
    assert result["categories"]["demo"]["status"] == "dry_run"
    assert result["scheduler_status"] == "ok"


def test_no_candidates_never_opens_the_browser(tmp_path, monkeypatch):
    """没有文章可采(或全被跳过)⇒ 不调用采集器 = 不开浏览器,也报得出"少发了多少请求"。"""
    spy = _install_spy(monkeypatch, plan=lambda category, db_path=None, limit=None: {
        "candidates": 0, "picked_ids": [], "host_skipped": 64, "known_gone_skipped": 4, "known_gone_ids": [1, 2, 3, 4]})
    result = scheduler.run_job(_job(tmp_path))
    assert "collect" not in spy.names(), "空轮次不得开浏览器"
    entry = result["categories"]["demo"]
    assert entry["status"] == "no_candidates"
    assert entry["known_gone_skipped"] == 4 and entry["host_skipped"] == 64
    assert result["scheduler_status"] == "ok"


# --------------------------------------------------------------------------- 二、让开浏览器:队列忙就整轮跳过

def test_metrics_round_skipped_while_crawl_queue_is_busy(tmp_path, monkeypatch):
    """调度进程与 Worker 是两个进程、共用同一个浏览器 profile ⇒ 队列里有 queued/running 就整轮跳过,
    一次请求都不发(宁可这一轮不采,也不能让两个进程抢同一个 profile)。"""
    spy = _install_spy(monkeypatch)
    system_db = _system_db(tmp_path)
    store.create_crawl_job("demo_source", "demo", db_path=system_db)
    result = scheduler.run_job(_job(tmp_path, system_db=str(system_db), wait_for_queue_s=0))

    assert result["scheduler_status"] == "skipped_queue_busy"
    assert "collect" not in spy.names() and "relabel" not in spy.names()
    assert result["queue"]["queued"] == 1


def test_metrics_round_waits_for_the_queue_to_drain(tmp_path, monkeypatch):
    """队列忙不能一票否决:先有界等待排空,等到就正常跑(否则自动回流会被一次撞车饿死)。"""
    spy = _install_spy(monkeypatch)
    system_db = _system_db(tmp_path)
    job_id = store.create_crawl_job("demo_source", "demo", db_path=system_db)

    naps: list[float] = []

    def fake_nap(seconds: float) -> None:
        naps.append(seconds)
        con = sqlite3.connect(system_db)
        con.execute("UPDATE crawl_jobs SET status='done' WHERE id=?", (job_id,))
        con.commit()
        con.close()

    monkeypatch.setattr(scheduler, "_nap", fake_nap)
    result = scheduler.run_job(_job(tmp_path, system_db=str(system_db), wait_for_queue_s=60, queue_poll_s=5))
    assert naps, "队列忙时必须等待,不能直接跳过"
    assert result["scheduler_status"] == "ok"
    assert "collect" in spy.names()
    assert result["queue"]["waited_s"] > 0


def test_non_crawl_queue_rows_do_not_block_metrics(tmp_path, monkeypatch):
    """Worker 永不认领 job_type != 'crawl' 的任务 ⇒ 它们不能把指标采集永久饿死。"""
    spy = _install_spy(monkeypatch)
    system_db = _system_db(tmp_path)
    con = sqlite3.connect(system_db)
    con.execute("INSERT INTO crawl_jobs(job_type, category, source, status, created_at) "
                "VALUES('other','demo','nobody','queued','2026-11-17T09:00:00+08:00')")
    con.commit()
    con.close()
    result = scheduler.run_job(_job(tmp_path, system_db=str(system_db), wait_for_queue_s=0))
    assert result["scheduler_status"] == "ok", "只有 crawl 类型任务才代表有人在用浏览器"
    assert "collect" in spy.names()


# --------------------------------------------------------------------------- 三、失败要留下明确状态

def test_collect_failure_does_not_run_downstream_steps(tmp_path, monkeypatch):
    def boom(category, db_path=None, limit=None, apply=False, **kwargs):
        raise RuntimeError("浏览器起不来")

    spy = _install_spy(monkeypatch, collect=boom)
    result = scheduler.run_job(_job(tmp_path))
    assert result["scheduler_status"] == "error"
    assert "relabel" not in spy.names() and "analyze" not in spy.names(), "采集失败不能接着改标签和分析口径"
    assert "浏览器起不来" in result["categories"]["demo"]["error"]


def test_run_due_records_the_declared_status_not_ok(tmp_path, monkeypatch):
    """真人要求:"失败必须留下明确状态,不能把失败任务伪装成成功" ⇒ 任务自己声明的更差状态
    必须进 scheduler_state.json,而不是被 run_due 一律写成 ok。"""
    def boom(category, db_path=None, limit=None, apply=False, **kwargs):
        raise RuntimeError("robots 不放行")

    _install_spy(monkeypatch, collect=boom)
    state_path = tmp_path / "scheduler_state.json"
    conf = {"scheduler": {"enabled": True, "jobs": [_job(tmp_path)]}}
    report = scheduler.run_due(state_path=state_path, conf=conf)
    assert report["ran"][0]["status"] == "error", "整体失败不能报 ok"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["metrics_fetch_6h"]["last_status"] == "error"
    assert state["metrics_fetch_6h"]["last_result"]["scheduler_status"] == "error"


def test_busy_skip_status_is_recorded_in_state(tmp_path, monkeypatch):
    _install_spy(monkeypatch)
    system_db = _system_db(tmp_path)
    store.create_crawl_job("demo_source", "demo", db_path=system_db)
    state_path = tmp_path / "scheduler_state.json"
    conf = {"scheduler": {"enabled": True, "jobs": [_job(tmp_path, system_db=str(system_db), wait_for_queue_s=0)]}}
    report = scheduler.run_due(state_path=state_path, conf=conf)
    assert report["ran"][0]["status"] == "skipped_queue_busy"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["metrics_fetch_6h"]["last_status"] == "skipped_queue_busy", "跳过要看得见,不能算成功也不能算失败"


# --------------------------------------------------------------------------- 四、类目隔离与证据

def test_one_failing_category_does_not_stop_the_others(tmp_path, monkeypatch):
    calls: list[str] = []

    def plan(category, db_path=None, limit=None):
        return {"candidates": 2, "picked_ids": [2, 1], "host_skipped": 0, "known_gone_skipped": 0, "known_gone_ids": []}

    def collect(category, db_path=None, limit=None, apply=False, **kwargs):
        calls.append(category)
        if category == "bad":
            raise PermissionError("来源未授权")
        return {"fetched": 2, "written": 2, "updated": 0, "snapshots": 2, "candidates": 2,
                "known_gone_skipped": 0, "outcomes": [], "outcome_counts": {"metrics_ok": 2}, "dry_run": False}

    spy = _install_spy(monkeypatch, plan=plan, collect=collect)
    job = _job(tmp_path, category=None)
    job.pop("category")
    monkeypatch.setattr(cfg, "load_config", lambda: {
        "metrics": {"public_pages": {"limit_per_run": 4, "sleep_s": 0.0}},
        "categories": ["good", "bad"],
    })
    result = scheduler.run_job(job)
    assert calls == ["good", "bad"], "按类目独立执行:一个类目失败不能带走其它类目"
    assert result["categories"]["good"]["status"] == "ok"
    assert result["categories"]["bad"]["status"] == "error"
    assert result["scheduler_status"] == "partial", "部分失败必须报 partial,不能算全绿"
    # 好的类目走完了闭环,坏的类目没有
    relabeled = [args[0] for args in spy.args_for("relabel")]
    assert relabeled == ["good"]


def test_evidence_file_written_for_every_category_even_on_failure(tmp_path, monkeypatch):
    def collect(category, db_path=None, limit=None, apply=False, **kwargs):
        raise RuntimeError("页面全打不开")

    _install_spy(monkeypatch, collect=collect)
    evidence = tmp_path / "evidence"
    scheduler.run_job(_job(tmp_path, evidence_dir=str(evidence)))
    files = sorted(evidence.glob("demo-*.json"))
    assert len(files) == 1, "失败轮次也要留证据,不然对账时两头都对不上"
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["category"] == "demo" and "页面全打不开" in payload["error"]


def test_evidence_written_on_a_successful_round(tmp_path, monkeypatch):
    _install_spy(monkeypatch)
    evidence = tmp_path / "evidence"
    scheduler.run_job(_job(tmp_path, evidence_dir=str(evidence)))
    files = sorted(evidence.glob("demo-*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["written"] == 3 and payload["outcome_counts"]["metrics_ok"] == 3


# --------------------------------------------------------------------------- 五、边界与红线

def test_metrics_fetch_is_allowed_but_writing_stays_forbidden():
    conf = {"scheduler": {"enabled": True, "jobs": [{"id": "m", "kind": "metrics_fetch", "interval_min": 360},
                                                 {"id": "c", "kind": "crawl_enqueue", "interval_min": 240}]}}
    jobs = scheduler.load_jobs(conf)
    assert len(jobs["jobs"]) == 2, "metrics_fetch 是本地分析类任务,不属于禁止清单"
    with pytest.raises(ValueError):
        scheduler.load_jobs({"scheduler": {"enabled": True, "jobs": [{"id": "w", "kind": "write", "interval_min": 60}]}})
    with pytest.raises(ValueError):
        scheduler.run_job({"id": "p", "kind": "publish"})


def test_shipped_config_has_a_bounded_metrics_job(tmp_path):
    """生产配置里的指标采集任务必须"有限速、有上限、排在 crawl_enqueue 之前"。

    排在前面是有意的:同一个 tick 里两个任务都到期时,先采集指标(此时队列必然空闲),
    再入队采集,避免两边撞同一个浏览器 profile。

    R42:取数时点改为按 ``due_at`` 排队,启用任务是 ``metrics_due``;
    旧的 ``metrics_fetch`` 只保留一条停用记录用于人工对照/回滚,不得同时启用两条。
    """
    conf = json.loads(Path(cfg.CONFIG_PATH).read_text(encoding="utf-8"))
    jobs = conf["scheduler"]["jobs"]
    due_jobs = [job for job in jobs if job.get("kind") == "metrics_due"]
    assert len(due_jobs) == 1, "调度器里应当有且只有一个按 due_at 排队的指标采集任务"
    job = due_jobs[0]
    assert job.get("enabled") is True and job.get("apply") is True
    page_cfg = conf["metrics"]["public_pages"]
    assert 0 < int(job["limit"]) <= int(page_cfg["limit_per_run"]), "每轮数量必须有上限"
    assert 60 <= int(job["interval_min"]) <= 1440, "间隔不得小于 1 小时,也不得超过 1 天"
    assert float(job["sleep_s"]) >= float(page_cfg["sleep_s"]), "请求间隔不得低于配置里的下限"
    assert float(job.get("wait_for_queue_s", 0)) > 0, "必须先让开采集队列"
    enabled_metrics = [
        other["id"] for other in jobs
        if other.get("kind") in {"metrics_due", "metrics_fetch"} and other.get("enabled") is True
    ]
    assert enabled_metrics == [job["id"]], "enabled 的指标采集任务只能有一条,避免两套时点口径并行"
    ids = [item["id"] for item in jobs]
    assert ids.index(job["id"]) < ids.index("crawl_enqueue_4h"), "指标采集要排在入队之前"


# --------------------------------------------------------------------------- 六、同步顺序(浏览器只准一边开)

def test_all_browser_work_finishes_before_any_caliber_sync(tmp_path, monkeypatch):
    """所有类目的页面采集必须排在任何口径同步之前。

    重标定之后的关键词评分会触发"关键词反哺"往采集队列里塞任务,Worker 是另一个进程、
    共用同一份 02_crawler/.browser/profile。顺序一旦反过来(采完 a 就同步 a),
    a 的反哺任务可能在 b 还在用浏览器时就被 Worker 抢走,两边同时开同一个 profile。
    """
    order: list[str] = []

    def plan(category, db_path=None, limit=None):
        order.append(f"plan:{category}")
        return {"candidates": 2, "picked_ids": [2, 1], "host_skipped": 0, "known_gone_skipped": 0,
                "known_gone_ids": []}

    def collect(category, db_path=None, limit=None, apply=False, **kwargs):
        order.append(f"collect:{category}")
        return {"fetched": 2, "written": 2, "updated": 0, "snapshots": 2, "candidates": 2,
                "known_gone_skipped": 0, "outcomes": [], "outcome_counts": {"metrics_ok": 2}, "dry_run": False}

    def relabel(category, **kwargs):
        order.append(f"relabel:{category}")
        return {"labeled": 2}

    def analyze(category, **kwargs):
        order.append(f"analyze:{category}")
        return {"keywords": 1}

    _install_spy(monkeypatch, plan=plan, collect=collect, relabel=relabel, analyze=analyze)
    job = _job(tmp_path, category=None)
    job.pop("category")
    monkeypatch.setattr(cfg, "load_config", lambda: {
        "metrics": {"public_pages": {"limit_per_run": 4, "sleep_s": 0.0}},
        "categories": ["a", "b"],
    })
    result = scheduler.run_job(job)
    assert order == ["plan:a", "collect:a", "plan:b", "collect:b",
                     "relabel:a", "analyze:a", "relabel:b", "analyze:b"], \
        f"浏览器必须全部收尾才开始同步口径,实际:{order}"
    assert result["scheduler_status"] == "ok"


def test_sync_failure_keeps_written_rows_visible_and_is_not_reported_ok(tmp_path, monkeypatch):
    """写库成功、口径同步失败:状态不能算 ok,但已写入的条数必须还能看见(否则对账会漏)。"""
    def relabel(category, **kwargs):
        raise RuntimeError("重标定炸了")

    spy = _install_spy(monkeypatch, relabel=relabel)
    result = scheduler.run_job(_job(tmp_path))
    row = result["categories"]["demo"]
    assert row["status"] == "written_sync_failed", "数据进了库但口径没同步,状态要说清是哪一段断了"
    assert row["written"] == 3, "已写入的条数不能因为后面失败就被抹掉"
    assert "重标定炸了" in row["sync_error"]
    assert spy.args_for("analyze") == [], "重标定失败就不该继续往下跑分析"
    assert result["scheduler_status"] == "error"
    assert result["errors"], "失败必须进 errors,不能只写在类目明细里"


# --------------------------------------------------------------------------- 七、阈值快照落后要说清

def test_stale_threshold_snapshot_is_flagged(tmp_path, monkeypatch):
    """实测样本涨上去了、阈值快照还是旧样本时的:回执必须提示,不能装作口径已经跟上。"""
    def analyze(category, **kwargs):
        return {"viral": {"tiers": {"measured": 177}, "calibration": {"sample": 165}}}

    _install_spy(monkeypatch, analyze=analyze)
    row = scheduler.run_job(_job(tmp_path))["categories"]["demo"]
    assert row["measured"] == 177
    hint = row.get("calibration_hint", "")
    assert "177" in hint and "165" in hint and "--calibrate" in hint
    assert row["status"] == "ok", "提示不是错误:数据已入库、口径按现有阈值同步完成"


def test_no_calibration_hint_when_sample_too_small(tmp_path, monkeypatch):
    """实测样本不足 30 条时阈值本来就没标定(如实拒绝),这种情形不必提示去标定。"""
    def analyze(category, **kwargs):
        return {"viral": {"tiers": {"measured": 22}, "calibration": {"sample": 0}}}

    _install_spy(monkeypatch, analyze=analyze)
    row = scheduler.run_job(_job(tmp_path))["categories"]["demo"]
    assert "calibration_hint" not in row


def test_no_calibration_hint_when_snapshot_is_up_to_date(tmp_path, monkeypatch):
    """阈值快照就是用当前样本标定的:不再提示。"""
    def analyze(category, **kwargs):
        return {"viral": {"tiers": {"measured": 40}, "calibration": {"sample": 40}}}

    _install_spy(monkeypatch, analyze=analyze)
    row = scheduler.run_job(_job(tmp_path))["categories"]["demo"]
    assert row["measured"] == 40 and "calibration_hint" not in row
