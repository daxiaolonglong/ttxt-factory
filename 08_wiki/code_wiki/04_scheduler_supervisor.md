# 04 · 守护体系：scheduler 与 supervisor

## 4.1 scheduler.py — B4 本地调度器

**立场**：只入队采集与跑本地分析，**绝不触发写作/发布/模型**。

- 任务定义：`00_control/config.json` 的 `scheduler.jobs`（`id/kind/interval_min(默认60)/enabled`）+ `scheduler.enabled`。当前配置 5 个任务：`metrics_fetch_6h`(360min, apply=true)、`crawl_enqueue_4h`(240min)、`analyze_daily`(1440min)、`recover_daily`(1440min)、`housekeep_weekly`(10080min)。
- `FORBIDDEN_KINDS = {"write","publish","model","model_drain","draft"}`（L26）：`load_jobs()`（L33）检测到即抛 ValueError（配置层禁止）；`run_job()`（L399）再挡一层。
- 状态文件：`00_control/scheduler_state.json`（`default_state_path()` L29），每 job 记 `last_run_at/last_status/last_result/last_error`；到期判定 `is_due()`（L73），`_parse_iso`（L60）统一转本地 naive 避免 aware/naive 混算。

### 任务注册表 `_JOB_RUNNERS`（L390）

| kind | 函数 | 行为 |
|---|---|---|
| `crawl_enqueue` | _job_crawl_enqueue:86 | 遍历 enabled 来源，`store.find_pending_crawl_job` 去重后 `create_crawl_job`（meta 标 `dispatched_via:"scheduler"`）；**只入队不执行** |
| `analyze` | _job_analyze:115 | 指定类目或全部注册类目跑 `analysis.analyze.run_analysis`，类目级错误隔离 |
| `recover` | _job_recover:134 | `model_queue.requeue_stale_running` + `store.recover_stale_crawl_jobs` + `requeue_failed_crawl_jobs` |
| `housekeep` | _job_housekeep:146 | `housekeep.run(clean=job.get("clean",False))` |
| `metrics_fetch` | _job_metrics_fetch:214 | P35 公开互动指标自动回流（见下） |

### metrics_fetch 三条硬口径（L152-159 注释）

1. 默认 dry-run，job 显式 `apply=true` 才写生产库；
2. 与 crawl Worker 共用浏览器 profile ⇒ 开跑前 `_wait_for_idle_queue()`（L190）有界等待（默认 `wait_for_queue_s=240`、`queue_poll_s=10`；配置里 300/20/300）；`crawl_queue_busy_counts()`（L167）只数 `job_type='crawl' AND status IN ('queued','running')`；忙则整轮 `skipped_queue_busy` 一个请求不发；`fetcher.BrowserBusyError` → `skipped_browser_busy`（让路不是故障，不重试不硬抢）；
3. 类目间互不牵连；写库后同步 `metrics_import.relabel_after_import` + `run_analysis`（重标定用现有阈值不自动重算；样本涨≥30 且阈值快照旧时给 `calibration_hint`）；证据每轮每类目落 `03_intelligence/metrics_public/<category>-<stamp>.json`（`_write_metrics_evidence()` L206，失败也落）。

整轮状态机：ok/partial/error/skipped_browser_busy——`_record_run()`（L409）允许任务声明比 ok 更差的 `scheduler_status`，**不得把失败伪装成成功**。

### 入口函数

- `run_due(now, state_path, conf)`（L432）：跑一轮到期任务并记账。
- `run_specific(job_id, ...)`（L448）：手动跑一个任务照常记账（CLI `schedule --run-job`，验证新任务不必等时钟）。
- `status_report(...)`（L460）：每 job 的 due 状态。
- `daemon(interval_s=300, ...)`（L483）：常驻轮询，sleep 下限 30s。

## 4.2 supervisor.py — P31 进程托管（Windows）

**背景**：解决 94 小时静默停摆——计划任务只保证"拉一次"，supervisor 保证"一直在"。

- 边界：`ALLOWED_CHILD_KINDS = {"schedule","worker"}`；`FORBIDDEN_CHILD_KINDS = {"write","publish","model","model_drain","draft","serve"}`（L44-45）——写作/发布/模型/面板一律禁止托管（与 scheduler 同一立场）。
- 默认参数（L47-53）：restart_delay 5s、max_restarts 10/600s 窗口、poll 1s、heartbeat 10s 写 / 900s 新鲜（doctor 判定）、scheduler_interval 300s；`policy(conf)`（L95）从 `config.json` 的 `supervisor` 段按词表覆盖——唯一口径来源。

### 锁/停/状态文件（均在 `00_control/`）

| 文件 | 机制 |
|---|---|
| `supervisor.lock` | `acquire_lock()`（L132）用 `msvcrt.locking(LK_NBLCK,1)` 区域锁——进程死亡 OS 自动释放，不留假锁；锁内字节不可读 ⇒ PID 另落侧文件 `supervisor.pid`（`pid_path_for()` L123） |
| `supervisor_stop` | 软停标志（`request_stop()/clear_stop()` L256/263）；存在 ⇒ supervise 终止子进程后退出，绝不伪造收尾 |
| `supervisor_state.json` | `read_state/write_state`（L237/248，tmp+replace 原子写）：`supervisor_pid/updated_at(心跳)/children[{name,kind,argv,pids,runs,restarts,exit_code,started_at,exited_at,giving_up,give_up_reason,terminated,killed}]/stop_reason/stopped_at` |

### 关键函数

- `supervisor_running(...)`（L212）：PID 侧文件 + `pid_alive()`（L185，Win32 `OpenProcess`+`WaitForSingleObject`）+ 心跳年龄（`stale_heartbeat`，stale_after_s=180）。
- `build_child_commands(...)`（L275）：两条 argv——`python -X utf8 -m ttxt schedule --daemon --interval-sec N` 与 `... worker --owner supervisor --max-jobs 0 --idle-stop-s 0`；extra_argv 含禁止词直接 ValueError。
- `supervise(...)`（L314）：核心看护循环——poll 子进程、退出即重启（退避 restart_delay_s）；窗口内重启 ≥ max_restarts ⇒ `giving_up=True, give_up_reason="restarts_exceeded(N/Xs)"`；worker 退出且 `crawler.worker.stop_path()` 存在 ⇒ **不重启**（`blocked_by_stop_flag`，尊重运维意图）；全部放弃 ⇒ `stop_reason="all_children_given_up"`；`run_for_s` 限时退出（验收演练）；收尾 terminate→12s 后 kill；每 heartbeat_s persist 状态。`start()`（L377）显式 `cwd=PROJECT_ROOT`（计划任务默认工作目录是 System32）。
- `run_forever(...)`（L477）：持锁常驻；已有托管 ⇒ `SupervisorAlreadyRunning`（L62）。
- `default_launcher(argv)`（L492）：`DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP` 无窗口拉起，stdout/stderr 追加到 `09_logs/supervisor.out.log` / `supervisor.err.log`。
- `ensure(launcher=None, ...)`（L505）：幂等——已在跑返回 `{started:False, reason:"already-running"}`；`supervisor_stop` 存在返回 `stop-flag-present`；否则无窗口拉起。**计划任务每 5 分钟调 `ttxt supervisor --ensure` 兜底**，反复调不会有两份。

### 运维速查

```powershell
ttxt supervisor --status          # 谁在跑、重启了几次、give_up 原因
ttxt supervisor --stop            # 软停（写 supervisor_stop）
ttxt supervisor --clear-stop      # 清除软停标志
ttxt supervisor --ensure          # 幂等拉起（计划任务用）
schtasks /create /tn TouTiaoSupervisorEnsure /tr "..." /sc minute /mo 5  # 兜底计划任务（见 scripts/setup_scheduler_task.ps1）
```
