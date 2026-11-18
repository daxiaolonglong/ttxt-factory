# -*- coding: utf-8 -*-
"""P31 阶段一:进程托管( supervisor )的回归测试。

背景:`ttxt schedule --daemon` 与 `ttxt worker` 都有实现,但没有任何常驻托管,
2026-11-12 19:57 之后两个进程都不在了,调度停摆约 94 小时。
本文件把"托管必须存在且可验证"钉成回归:

- 只允许托管 schedule / worker 两类进程,write/publish/model/serve 一律禁止;
- 单实例(区域锁 + PID 存活判定),第二个实例必须被拒;
- 子进程异常退出要记录退出码并自动重启,超过阈值放弃并写原因;
- 停止标志能优雅收尾,状态文件留下 pid/启动时间/退出码/重启次数。

测试用真实子进程(临时脚本)与临时目录,不连接任何生产库,不触发采集。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from ttxt import supervisor as sup


ROOT = Path(__file__).resolve().parents[1]


def _paths(tmp_path: Path) -> dict:
    return {
        "lock_path": tmp_path / "supervisor.lock",
        "state_path": tmp_path / "supervisor_state.json",
        "stop_path": tmp_path / "supervisor_stop",
    }


def test_module_exposes_public_surface() -> None:
    for name in ("SupervisorAlreadyRunning", "acquire_lock", "release_lock", "pid_alive",
                 "supervisor_running", "build_child_commands", "supervise", "run_forever",
                 "ensure", "request_stop", "clear_stop", "read_state", "write_state",
                 "ALLOWED_CHILD_KINDS", "FORBIDDEN_CHILD_KINDS"):
        assert hasattr(sup, name), f"缺少 {name}"


def test_only_schedule_and_worker_may_be_hosted() -> None:
    children = sup.build_child_commands()
    names = [c["name"] for c in children]
    assert names == ["scheduler", "worker"], f"托管对象不对: {names}"
    for child in children:
        argv_text = " ".join(child["argv"])
        assert " -m ttxt" in argv_text or "-m ttxt" in argv_text, argv_text
        for forbidden in sup.FORBIDDEN_CHILD_KINDS:
            assert forbidden not in child["argv"], f"{child['name']} 里出现禁止托管的 {forbidden}"
    assert any("--daemon" in c["argv"] for c in children if c["name"] == "scheduler"), \
        "调度子进程必须常驻轮询,不能只跑一轮"
    worker = next(c for c in children if c["name"] == "worker")
    assert "--idle-stop-s" in worker["argv"], "Worker 不能因为队列空闲就自己退出"
    assert worker["argv"][worker["argv"].index("--idle-stop-s") + 1] == "0"
    assert "--max-jobs" in worker["argv"]
    assert worker["argv"][worker["argv"].index("--max-jobs") + 1] == "0", "Worker 不能跑一单就退出"
    assert sup.ALLOWED_CHILD_KINDS == {"schedule", "worker"}


def test_child_commands_are_still_guarded_against_forbidden_kinds() -> None:
    assert "write" in sup.FORBIDDEN_CHILD_KINDS
    assert "publish" in sup.FORBIDDEN_CHILD_KINDS
    assert "model" in sup.FORBIDDEN_CHILD_KINDS
    with pytest.raises(ValueError):
        sup.build_child_commands(extra_argv=["write"])


def test_children_run_from_the_project_root(tmp_path: Path) -> None:
    """子进程是 ``-m ttxt ...``,工作目录必须是项目根。

    实测踩过的坑:计划任务默认工作目录是 ``C:\\Windows\\System32``,``-m ttxt`` 直接
    ``No module named ttxt``,托管看起来起来了但什么都没跑。
    """
    from ttxt import config as cfg

    paths = _paths(tmp_path)
    marker = tmp_path / "cwd.txt"
    probe = [sys.executable, "-c",
             "import os,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(os.getcwd())", str(marker)]
    sup.supervise(children=[{"name": "cwd-probe", "argv": probe}],
                  run_for_s=1.0, poll_s=0.1, restart_delay_s=0.05, max_restarts=1, **paths)
    assert marker.exists(), "子进程必须真的跑起来,否则托管就是空转"
    assert Path(marker.read_text(encoding="utf-8")).resolve() == Path(cfg.PROJECT_ROOT).resolve()


def test_crash_is_recorded_and_restarted_then_gives_up(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    crasher = [sys.executable, "-c", "raise SystemExit(3)"]
    report = sup.supervise(
        children=[{"name": "crasher", "argv": crasher}],
        restart_delay_s=0.05,
        max_restarts=2,
        poll_s=0.05,
        **paths,
    )
    child = report["children"][0]
    assert child["restarts"] == 2, f"应重启两次: {child}"
    assert child["giving_up"] is True, "超过重启阈值必须放弃,不能无限重启"
    assert child["exit_code"] == 3, f"退出码必须记录: {child}"
    assert child["pids"] and all(isinstance(p, int) for p in child["pids"])
    assert child["runs"] == 3, f"首次 + 2 次重启 = 3 次运行: {child}"
    assert report["giving_up"] == ["crasher"]
    state = sup.read_state(paths["state_path"])
    assert state["children"][0]["exit_code"] == 3
    assert state["supervisor_pid"] == sup.os.getpid()


def test_stop_flag_stops_children_gracefully(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    sleeper = [sys.executable, "-c", "import time; time.sleep(30)"]
    started = time.time()
    report = sup.supervise(
        children=[{"name": "sleeper", "argv": sleeper}],
        run_for_s=1.0,
        poll_s=0.1,
        **paths,
    )
    assert time.time() - started < 15, "停止后不能傻等子进程自己睡完"
    assert report["stop_reason"] == "run_for_s"
    child = report["children"][0]
    assert child["exit_code"] is not None, "子进程必须真的终止并记录退出码"
    assert child["terminated"] is True
    assert child["restarts"] == 0, "正常停止不该被算成崩溃重启"
    assert sup.read_state(paths["state_path"])["stopped_at"]


def test_second_instance_is_refused(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    handle = sup.acquire_lock(paths["lock_path"])
    assert handle is not None, "第一次必须拿到锁"
    try:
        assert sup.acquire_lock(paths["lock_path"]) is None, "第二份锁必须拿不到"
        with pytest.raises(sup.SupervisorAlreadyRunning):
            sup.run_forever(children=[{"name": "noop", "argv": [sys.executable, "-c", "pass"]}],
                            run_for_s=0.2, restart_delay_s=0.02, poll_s=0.02, max_restarts=3,
                            **paths)
    finally:
        sup.release_lock(handle)


def test_running_detection_uses_pid_not_the_lock_file_name(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    assert sup.pid_alive(0) is False
    assert sup.pid_alive(sup.os.getpid()) is True
    alive, info = sup.supervisor_running(paths["lock_path"], paths["state_path"])
    assert alive is False, "还没有常驻进程时不能谎报在跑"
    handle = sup.acquire_lock(paths["lock_path"])
    try:
        alive, info = sup.supervisor_running(paths["lock_path"], paths["state_path"])
        assert alive is True and info["pid"] == sup.os.getpid(), info
    finally:
        sup.release_lock(handle)


def test_ensure_starts_only_when_nothing_is_running(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    launched: list[list[str]] = []

    def fake_launcher(argv: list[str]) -> int:
        launched.append(argv)
        return 4242

    result = sup.ensure(launcher=fake_launcher, **paths)
    assert result["started"] is True and result["pid"] == 4242
    assert launched[0][-2:] == ["supervisor", "--run"], launched[0]

    handle = sup.acquire_lock(paths["lock_path"])
    try:
        again = sup.ensure(launcher=fake_launcher, **paths)
        assert again["started"] is False, "锁还在手上就不能再起第二份"
        assert again["reason"] == "already-running"
        assert len(launched) == 1, "已存在时不得重复拉起"
    finally:
        sup.release_lock(handle)


def test_stop_flag_file_roundtrip(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    assert not paths["stop_path"].exists()
    sup.request_stop(paths["stop_path"])
    assert paths["stop_path"].exists()
    sup.clear_stop(paths["stop_path"])
    assert not paths["stop_path"].exists()


def test_state_file_is_robust_against_garbage(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths["state_path"].write_text("{ 坏的 json", encoding="utf-8")
    assert sup.read_state(paths["state_path"]) == {}
    assert sup.supervisor_running(paths["lock_path"], paths["state_path"])[0] is False


def test_watchdog_installer_repetition_has_duration() -> None:
    """重复触发器必须带 Duration,否则统一调度引擎会判无效、只走网格不启动。

    2026-11-18 实测:缺 Duration 时 schtasks 报 "Repeat: Until: Duration: Disabled",
    NextRunTime 每 5 分钟推进而 LastRunTime 冻结,NumberOfMissedRuns 连续增长。
    """
    script = (Path(__file__).resolve().parents[1] / "scripts" / "install-supervisor-tasks.ps1").read_text(
        encoding="utf-8"
    )
    repetition = script.split("<Repetition>", 1)[1].split("</Repetition>", 1)[0]
    assert "<Interval>PT5M</Interval>" in repetition, repetition
    assert "<Duration>P1D</Duration>" in repetition, "缺 Duration 的重复触发器不会真正启动"


def test_production_paths_are_under_control_dir() -> None:
    # 默认路径必须落在 00_control,方便 doctor 与面板读取。
    assert "00_control" in str(sup.default_state_path())
    assert "00_control" in str(sup.default_stop_path())
    assert "00_control" in str(sup.default_lock_path())


def test_cli_exposes_supervisor_entry() -> None:
    from ttxt.__main__ import build_parser

    parser = build_parser()
    for flag in ("--run", "--ensure", "--status", "--stop", "--clear-stop"):
        args = parser.parse_args(["supervisor", flag])
        assert args.func.__name__ == "cmd_supervisor", flag
        assert not hasattr(args, "write") and not hasattr(args, "publish")
    args = parser.parse_args(["supervisor"])
    assert args.func.__name__ == "cmd_supervisor"


# ------------------------------------------------------------------ 参数与体检口径


def test_policy_takes_overrides_from_config_block() -> None:
    pol = sup.policy({"supervisor": {"max_restarts": 3, "restart_delay_s": 0.1, "乱写的键": 1}})
    assert pol["max_restarts"] == 3, pol
    assert pol["restart_delay_s"] == 0.1, pol
    assert "乱写的键" not in pol, "未知配置键不得混进策略"
    default = sup.policy({})
    assert default["max_restarts"] == sup.DEFAULT_MAX_RESTARTS
    assert default["worker_owner"] == sup.WORKER_OWNER


def _conf(jobs: list[dict]) -> dict:
    return {"scheduler": {"enabled": True, "jobs": jobs}}


def test_doctor_shows_the_stall_instead_of_hiding_it(tmp_path: Path) -> None:
    """停摆必须是体检里看得见的 FAIL,而不是"看起来一切正常"。"""
    from datetime import datetime, timedelta

    from ttxt import doctor as dc

    paths = _paths(tmp_path)
    stale = tmp_path / "scheduler_state.json"
    old = (datetime.now() - timedelta(hours=94)).isoformat()
    stale.write_text(json.dumps({"crawl_enqueue_4h": {"last_run_at": old, "last_status": "ok"}}),
                     encoding="utf-8")
    conf = _conf([{"id": "crawl_enqueue_4h", "kind": "crawl_enqueue", "interval_min": 240, "enabled": True}])
    levels = {name: (level, detail) for level, name, detail in
              dc.supervisor_checks(conf, scheduler_state_path=stale, **paths)}
    assert levels["ops.supervisor"][0] == "FAIL", levels
    assert "停摆" in levels["ops.supervisor"][1]
    assert levels["ops.scheduler_heartbeat"][0] == "FAIL", levels
    assert "94" in levels["ops.scheduler_heartbeat"][1], levels["ops.scheduler_heartbeat"]


def test_doctor_passes_when_hosted_and_jobs_are_fresh(tmp_path: Path) -> None:
    from ttxt import config as cfg
    from ttxt import doctor as dc

    paths = _paths(tmp_path)
    conf = _conf([{"id": "crawl_enqueue_4h", "kind": "crawl_enqueue", "interval_min": 240, "enabled": True}])
    fresh = tmp_path / "scheduler_state.json"
    fresh.write_text(json.dumps({"crawl_enqueue_4h": {"last_run_at": cfg.now_iso(), "last_status": "ok"}}),
                     encoding="utf-8")
    handle = sup.acquire_lock(paths["lock_path"])
    assert handle is not None
    try:
        sup.write_state({
            "supervisor_pid": sup.os.getpid(),
            "updated_at": cfg.now_iso(),
            "children": [{"name": "scheduler", "pids": [1234], "runs": 1, "restarts": 0,
                          "exit_code": None, "giving_up": False}],
        }, paths["state_path"])
        levels = {name: (level, detail) for level, name, detail in
                  dc.supervisor_checks(conf, scheduler_state_path=fresh, **paths)}
        assert levels["ops.supervisor"][0] == "PASS", levels
        assert levels["ops.scheduler_heartbeat"][0] == "PASS", levels
        assert "scheduler:pid=1234" in levels["ops.supervisor"][1]
    finally:
        sup.release_lock(handle)


def test_doctor_rejects_forbidden_job_kinds(tmp_path: Path) -> None:
    from ttxt import doctor as dc

    paths = _paths(tmp_path)
    conf = _conf([{"id": "writer", "kind": "write", "interval_min": 60, "enabled": True}])
    rows = dc.supervisor_checks(conf, **paths)
    levels = {name: level for level, name, _ in rows}
    assert levels["ops.scheduler_jobs"] == "FAIL", rows
